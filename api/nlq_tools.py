"""
자연어 질의(/query)에서 LLM이 호출하는 읽기 전용 도구

LLM은 SQL을 만들지 않고, 여기 정의된 함수만 이름·인자로 골라 호출한다.
각 도구는 기존 조회 API(/equipment, /oee, /quality/defect-summary, /anomalies)와
같은 계산 코드를 재사용하며, 인자는 실행 전에 검증한다.

- 설비 ID는 등록된 것만 허용 (빈 배열이면 전체 설비)
- 기간은 시간대가 있는 ISO 8601, start < end, 최대 MAX_RANGE_DAYS일
- 이상 판정 목록은 설비당 최근 MAX_ANOMALIES_PER_EQUIPMENT건까지만 돌려준다 (LLM 입력 크기 제한)
"""
from datetime import datetime, timedelta
from typing import Any, Dict, List

from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

import models
from anomaly import summarize_anomalies
from oee import calculate_oee
from quality import calculate_defect_summary

MAX_RANGE_DAYS = 30
MAX_ANOMALIES_PER_EQUIPMENT = 20

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
        "description": "기간 내 설비별·불량유형별 불량 집계(품질 이벤트 건수, 불량 수량 합)를 조회한다. "
                       "불량 유형: scratch, dimension_out, burr, discoloration.",
        "parameters": _range_schema(),
    },
    {
        "name": "get_anomalies",
        "description": "기간 내 설비별 급변 이상탐지 결과(판정 건수, 이상 판정 건수, 최대 이상 점수, 최근 이상 판정 목록)를 조회한다. "
                       "점진적 열화 경보 이력은 DB에 없어 이 도구로는 조회할 수 없다.",
        "parameters": _range_schema(),
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
    return {"start": start, "end": end, "results": calculate_defect_summary(db, targets, start, end)}


def _get_anomalies(db: Session, args: dict) -> dict:
    start, end = _parse_range(args)
    targets = _resolve_equipment(db, args.get("equipment_ids"))
    results = summarize_anomalies(db, targets, start, end)
    for row in results:
        total = len(row["anomalies"])
        row["anomalies"] = row["anomalies"][:MAX_ANOMALIES_PER_EQUIPMENT]
        row["anomalies_omitted"] = total - len(row["anomalies"])
    return {"start": start, "end": end, "results": results}


_HANDLERS = {
    "list_equipment": _list_equipment,
    "get_oee": _get_oee,
    "get_defect_summary": _get_defect_summary,
    "get_anomalies": _get_anomalies,
}


def execute_tool(db: Session, name: str, args: Dict[str, Any]) -> dict:
    """도구 실행 -> JSON으로 직렬화 가능한 dict. 잘못된 인자면 ToolError."""
    handler = _HANDLERS.get(name)
    if handler is None:
        raise ToolError(f"알 수 없는 도구: {name}. 사용 가능: {sorted(TOOL_NAMES)}")
    if not isinstance(args, dict):
        raise ToolError("인자는 객체(JSON object)여야 합니다")
    return jsonable_encoder(handler(db, args))
