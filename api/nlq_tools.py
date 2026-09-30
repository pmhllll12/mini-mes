"""
자연어 질의(/query)에서 LLM이 호출하는 읽기 전용 도구

LLM은 SQL을 만들지 않고, 여기 정의된 함수만 이름·인자로 골라 호출한다.
각 도구는 기존 조회 API(/equipment, /oee, /quality/defect-summary, /anomalies, /drift-alarms, /reports/daily)와
같은 계산 코드를 재사용하며, 인자는 실행 전에 검증한다. 모든 도구는 읽기 전용이다 (일일 리포트도 생성하지 않고 조회만).

- 설비 ID는 등록된 것만 허용 (빈 배열이면 전체 설비)
- 기간은 시간대가 있는 ISO 8601, start < end, 최대 MAX_RANGE_DAYS일
- 이상 판정·열화 경보 목록은 설비당 최근 MAX_ANOMALIES_PER_EQUIPMENT건까지만 돌려준다 (LLM 입력 크기 제한)
- 수량·건수·시간 필드는 이름에 단위를 드러낸다 (LLM이 불량 수량(개)을 건수로 말하던 문제의 대책)
- 결과의 시각은 모두 KST로 바꿔 돌려준다. DB 시각(UTC)을 그대로 주면 Gemini가 KST 조회 기간(15:06~16:06+09:00)과
  UTC 경보 시각(06:28Z)을 비교해 경보가 11건 있는데 "없다"고 답했다 (2026-09-30 평가 drift_history)
"""
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List

from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

import models
from anomaly import list_drift_alarms, summarize_anomalies
from oee import calculate_oee
from quality import calculate_defect_summary
from report import load_daily_reports

MAX_RANGE_DAYS = 30
MAX_ANOMALIES_PER_EQUIPMENT = 20
KST = timezone(timedelta(hours=9))

_RANGE_PROPERTIES = {
    "equipment_ids": {
        "type": "array",
        "items": {"type": "string"},
        "description": "조회할 설비 ID 목록 (예: [\"EQ-001\"]). 빈 배열이면 등록된 전체 설비",
    },
    "start": {
        "type": "string",
        "description": "조회 시작 시각. 시간대를 포함한 ISO 8601 (예: 2026-09-27T00:00:00+09:00)",
    },
    "end": {
        "type": "string",
        "description": "조회 끝 시각. 시간대를 포함한 ISO 8601. start보다 뒤이고 기간은 최대 30일",
    },
}


def _range_schema() -> dict:
    return {
        "type": "object",
        "properties": dict(_RANGE_PROPERTIES),
        "required": ["equipment_ids", "start", "end"],
        "additionalProperties": False,
    }


def _date_range_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "equipment_ids": _RANGE_PROPERTIES["equipment_ids"],
            "start_date": {"type": "string", "description": "시작 날짜 (KST, YYYY-MM-DD)"},
            "end_date": {"type": "string", "description": "끝 날짜 (KST, YYYY-MM-DD, 포함). 기간은 최대 31일"},
        },
        "required": ["equipment_ids", "start_date", "end_date"],
        "additionalProperties": False,
    }


# 제공자와 무관한 도구 정의 (name, description, JSON Schema). 각 LLM 어댑터가 자기 형식으로 변환한다.
TOOL_SPECS: List[dict] = [
    {
        "name": "list_equipment",
        "description": "등록된 설비 목록(설비 ID, 이름, 라인, 상태)을 조회한다. "
                       "질문에 설비 이름이나 라인만 나오면 이 도구로 설비 ID를 먼저 확인한다.",
        "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    {
        "name": "get_oee",
        "description": "기간 내 설비별 OEE(종합설비효율), 가동률, 양품률, 총 생산 수량, 불량 수량을 조회한다.",
        "parameters": _range_schema(),
    },
    {
        "name": "get_defect_summary",
        "description": "기간 내 설비별·불량유형별 불량 집계를 조회한다. 결과의 품질이벤트_건수(단위 건)와 "
                       "불량수량_개(단위 개, 이벤트에 연결된 생산실적의 불량 수량 합)는 서로 다른 값이다. "
                       "불량 유형: scratch, dimension_out, burr, discoloration.",
        "parameters": _range_schema(),
    },
    {
        "name": "get_anomalies",
        "description": "기간 내 설비별 급변 이상탐지 결과(판정 건수, 이상 판정 건수, 최대 이상 점수, 최근 이상 판정 목록)를 조회한다. "
                       "점진적 열화 경보 이력은 get_drift_alarms로 조회한다.",
        "parameters": _range_schema(),
    },
    {
        "name": "get_drift_alarms",
        "description": "기간과 겹치는 점진적 열화 경보 이력(설비별 경보 시작·해제 시각, 경보 중 여부, 지속시간_초, 시작 점수)을 조회한다. "
                       "해제_시각이 null이면 지금도 경보 중이다.",
        "parameters": _range_schema(),
    },
    {
        "name": "get_daily_reports",
        "description": "저장된 일일 리포트(KST 하루, 설비별 가동률·양품률·OEE(0~1 비율), 총생산_개, 불량_개, 최다 불량 유형, "
                       "급변 이상 건수, 열화 경보 횟수·지속 초)를 날짜 범위로 조회한다. 리포트는 매일 새벽 전날분이 만들어지며, "
                       "만들어지지 않은 날짜는 리포트_없는_날짜로 알려준다 (이 도구는 리포트를 새로 만들지 않는다).",
        "parameters": _date_range_schema(),
    },
]
TOOL_NAMES = {spec["name"] for spec in TOOL_SPECS}


class ToolError(Exception):
    """도구 인자가 잘못됐을 때. 메시지는 LLM에게 그대로 돌려줘 스스로 고치게 한다."""


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str):
        raise ToolError(f"{field}는 ISO 8601 문자열이어야 합니다")
    try:
        ts = datetime.fromisoformat(value)
    except ValueError:
        raise ToolError(f"{field} 형식이 잘못됐습니다: {value!r} (예: 2026-09-27T00:00:00+09:00)")
    if ts.tzinfo is None:
        raise ToolError(f"{field}에 시간대가 없습니다: {value!r} (예: +09:00)")
    return ts


def _parse_range(args: dict) -> tuple[datetime, datetime]:
    start = _parse_time(args.get("start"), "start")
    end = _parse_time(args.get("end"), "end")
    if start >= end:
        raise ToolError("start는 end보다 앞이어야 합니다")
    if end - start > timedelta(days=MAX_RANGE_DAYS):
        raise ToolError(f"조회 기간은 최대 {MAX_RANGE_DAYS}일입니다")
    return start, end


MAX_REPORT_DAYS = 31


def _parse_date_range(args: dict) -> tuple[date, date]:
    parsed = []
    for field in ("start_date", "end_date"):
        value = args.get(field)
        try:
            parsed.append(date.fromisoformat(value))
        except (TypeError, ValueError):
            raise ToolError(f"{field} 형식이 잘못됐습니다: {value!r} (예: 2026-09-28)")
    start, end = parsed
    if start > end:
        raise ToolError("start_date는 end_date보다 늦을 수 없습니다")
    if (end - start).days + 1 > MAX_REPORT_DAYS:
        raise ToolError(f"조회 기간은 최대 {MAX_REPORT_DAYS}일입니다")
    return start, end


def _resolve_equipment(db: Session, requested: Any) -> List[str]:
    registered = [row.equipment_id for row in db.query(models.Equipment.equipment_id).order_by(models.Equipment.equipment_id)]
    if requested in (None, []):
        return registered
    if not isinstance(requested, list) or not all(isinstance(x, str) for x in requested):
        raise ToolError("equipment_ids는 문자열 배열이어야 합니다")
    unknown = [x for x in requested if x not in registered]
    if unknown:
        raise ToolError(f"등록되지 않은 설비 ID: {unknown}. 등록된 설비: {registered}")
    return list(dict.fromkeys(requested))


def _list_equipment(db: Session, args: dict) -> dict:
    rows = db.query(models.Equipment).order_by(models.Equipment.equipment_id).all()
    return {"equipment": [
        {"equipment_id": r.equipment_id, "name": r.name, "line_id": r.line_id, "status": r.status} for r in rows
    ]}


def _get_oee(db: Session, args: dict) -> dict:
    start, end = _parse_range(args)
    targets = _resolve_equipment(db, args.get("equipment_ids"))
    return {"start": start, "end": end, "results": [calculate_oee(db, eq, start, end) for eq in targets]}


def _get_defect_summary(db: Session, args: dict) -> dict:
    start, end = _parse_range(args)
    targets = _resolve_equipment(db, args.get("equipment_ids"))
    # LLM이 불량 수량(개)을 건수로 말하던 문제(평가 defect_top_today)로, 필드 이름에 단위를 드러낸다.
    # 조회 API(/quality/defect-summary)의 응답 형식은 그대로 둔다
    rows = [
        {
            "equipment_id": r["equipment_id"],
            "defect_type": r["defect_type"],
            "품질이벤트_건수": r["event_count"],
            "불량수량_개": r["total_qty_defect"],
        }
        for r in calculate_defect_summary(db, targets, start, end)
    ]
    return {"start": start, "end": end, "results": rows}


def _get_anomalies(db: Session, args: dict) -> dict:
    start, end = _parse_range(args)
    targets = _resolve_equipment(db, args.get("equipment_ids"))
    results = summarize_anomalies(db, targets, start, end)
    for row in results:
        total = len(row["anomalies"])
        row["anomalies"] = row["anomalies"][:MAX_ANOMALIES_PER_EQUIPMENT]
        # 로컬 모델이 anomalies_omitted를 "무시된 이상"으로 해석해, 목록 길이 제한 때문에 빠진 건수임을 이름에 드러낸다
        row["목록에서_생략된_이상_건수"] = total - len(row["anomalies"])
    return {"start": start, "end": end, "results": results}


def _get_drift_alarms(db: Session, args: dict) -> dict:
    start, end = _parse_range(args)
    targets = _resolve_equipment(db, args.get("equipment_ids"))
    alarms = {eq: [] for eq in targets}
    for a in list_drift_alarms(db, targets, start, end):
        alarms[a["equipment_id"]].append({
            "시작_시각": a["raised_ts"],
            "해제_시각": a["cleared_ts"],
            "경보_중": a["active"],
            "지속시간_초": round(a["duration_sec"], 1) if a["duration_sec"] is not None else None,
            "시작_점수": a["raised_score"],
            "threshold": a["threshold"],
        })
    results = []
    for eq in targets:
        rows = alarms[eq]
        results.append({
            "equipment_id": eq,
            "경보_횟수": len(rows),
            "경보_중": any(r["경보_중"] for r in rows),
            "경보_목록": rows[-MAX_ANOMALIES_PER_EQUIPMENT:],   # 최근 것 위주
            "목록에서_생략된_경보_수": max(len(rows) - MAX_ANOMALIES_PER_EQUIPMENT, 0),
        })
    return {"start": start, "end": end, "results": results}


def _get_daily_reports(db: Session, args: dict) -> dict:
    start, end = _parse_date_range(args)
    targets = _resolve_equipment(db, args.get("equipment_ids"))
    reports = load_daily_reports(db, start, end, targets)
    found = {r.report_date for r in reports}
    days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
    return {
        "start_date": start,
        "end_date": end,
        "results": [
            {
                "report_date": r.report_date,
                "equipment_id": r.equipment_id,
                "가동률": float(r.availability),
                "양품률": float(r.quality_rate),
                "OEE": float(r.oee),
                "총생산_개": r.total_qty,
                "불량_개": r.total_defect,
                "최다불량_유형": r.top_defect_type,
                "최다불량_이벤트_건수": r.top_defect_events,
                "최다불량_수량_개": r.top_defect_qty,
                "급변판정_건수": r.anomaly_scored,
                "급변이상_건수": r.anomaly_count,
                "열화경보_횟수": r.drift_alarms,
                "열화경보_지속_초": float(r.drift_alarm_sec),
            }
            for r in reports
        ],
        "리포트_없는_날짜": [d for d in days if d not in found],
    }


_HANDLERS = {
    "list_equipment": _list_equipment,
    "get_oee": _get_oee,
    "get_defect_summary": _get_defect_summary,
    "get_anomalies": _get_anomalies,
    "get_drift_alarms": _get_drift_alarms,
    "get_daily_reports": _get_daily_reports,
}


def execute_tool(db: Session, name: str, args: Dict[str, Any]) -> dict:
    """도구 실행 -> JSON으로 직렬화 가능한 dict. 잘못된 인자면 ToolError."""
    handler = _HANDLERS.get(name)
    if handler is None:
        raise ToolError(f"알 수 없는 도구: {name}. 사용 가능: {sorted(TOOL_NAMES)}")
    if not isinstance(args, dict):
        raise ToolError("인자는 객체(JSON object)여야 합니다")
    return jsonable_encoder(_to_kst(handler(db, args)))


def _to_kst(value):
    """결과 안의 시간대 있는 datetime을 KST로 (시간대 없는 값·날짜는 그대로)"""
    if isinstance(value, datetime) and value.tzinfo is not None:
        return value.astimezone(KST)
    if isinstance(value, dict):
        return {k: _to_kst(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_kst(v) for v in value]
    return value
