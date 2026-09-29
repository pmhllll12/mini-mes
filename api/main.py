import csv
import io
import os
import time
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from prometheus_client import generate_latest, CONTENT_TYPE_LATEST
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db, engine, Base
import models
import schemas
from oee import calculate_oee
from quality import calculate_defect_summary
from anomaly import DRIFT_ALARM_CSV_COLUMNS, list_drift_alarms, summarize_anomalies
from report import REPORT_COLUMNS, build_daily_report, load_daily_reports, save_daily_report, yesterday_kst
from nlq_providers import ClaudeProvider, GeminiProvider, NLQProviderError, user_message
from nlq_tools import TOOL_NAMES, ToolError, execute_tool

# 로컬 개발 편의를 위해 앱 시작 시 테이블 자동 생성
# (운영에서는 schema.sql / 마이그레이션 도구를 통해 관리)
Base.metadata.create_all(bind=engine)

# metrics 모듈은 import 시점(REGISTRY.register)에 곧바로 DB를 조회하므로,
# 테이블이 만들어지는 create_all 이후에 import해야 한다.
from metrics import HTTP_REQUEST_COUNT, HTTP_REQUEST_LATENCY, NLQ_REQUESTS, NLQ_TOOL_CALLS  # noqa: E402

app = FastAPI(title="mini-mes", version="0.1.0")


@app.middleware("http")
async def prometheus_http_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    duration = time.perf_counter() - start

    route = request.scope.get("route")
    path = route.path if route else request.url.path  # path 템플릿 사용 (라벨 카디널리티 억제)
    HTTP_REQUEST_COUNT.labels(request.method, path, response.status_code).inc()
    HTTP_REQUEST_LATENCY.labels(request.method, path).observe(duration)
    return response


@app.get("/metrics")
def metrics_endpoint():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# 설비 status 규칙:
# - 생산실적 수신 시 qty_good+qty_defect > 0 이면 running, 0이면 stopped로 갱신한다.
# - 마지막 생산실적 이후 STALE_MINUTES 이상 새 데이터가 없으면 조회 시점에 stopped로 간주한다.
# - 생산실적이 한 번도 없었던 설비는 시드 상태(idle)를 그대로 보여준다.
STALE_MINUTES = 5


@app.get("/health")
def health():
    return {"status": "ok"}


def _effective_status(stored_status: str, last_log_ts: Optional[datetime]) -> str:
    if last_log_ts is not None and datetime.now(timezone.utc) - last_log_ts > timedelta(minutes=STALE_MINUTES):
        return "stopped"
    return stored_status


# ---------- 설비 ----------

@app.get("/equipment", response_model=List[schemas.EquipmentStatus])
def list_equipment(db: Session = Depends(get_db)):
    equipments = db.query(models.Equipment).all()
    latest_ts_by_equipment = dict(
        db.query(models.ProductionLog.equipment_id, func.max(models.ProductionLog.ts))
        .group_by(models.ProductionLog.equipment_id)
        .all()
    )
    return [
        schemas.EquipmentStatus(
            equipment_id=eq.equipment_id,
            name=eq.name,
            line_id=eq.line_id,
            status=_effective_status(eq.status, latest_ts_by_equipment.get(eq.equipment_id)),
        )
        for eq in equipments
    ]


@app.get("/equipment/{equipment_id}", response_model=schemas.EquipmentStatus)
def get_equipment(equipment_id: str, db: Session = Depends(get_db)):
    eq = db.query(models.Equipment).filter_by(equipment_id=equipment_id).first()
    if not eq:
        raise HTTPException(status_code=404, detail="equipment not found")

    last_log_ts = (
        db.query(func.max(models.ProductionLog.ts))
        .filter(models.ProductionLog.equipment_id == equipment_id)
        .scalar()
    )
    return schemas.EquipmentStatus(
        equipment_id=eq.equipment_id,
        name=eq.name,
        line_id=eq.line_id,
        status=_effective_status(eq.status, last_log_ts),
    )


# ---------- 생산실적 수집 (설비/시뮬레이터가 호출) ----------

@app.post("/production-logs", response_model=schemas.ProductionLogOut, status_code=201)
def create_production_log(payload: schemas.ProductionLogIn, db: Session = Depends(get_db)):
    eq = db.query(models.Equipment).filter_by(equipment_id=payload.equipment_id).first()
    if not eq:
        raise HTTPException(status_code=404, detail="unknown equipment_id")

    log = models.ProductionLog(**payload.model_dump())
    db.add(log)

    eq.status = "running" if (payload.qty_good + payload.qty_defect) > 0 else "stopped"

    db.commit()
    db.refresh(log)
    return log


# ---------- 품질 이벤트 수집 ----------

@app.post("/quality-events", response_model=schemas.QualityEventOut, status_code=201)
def create_quality_event(payload: schemas.QualityEventIn, db: Session = Depends(get_db)):
    eq = db.query(models.Equipment).filter_by(equipment_id=payload.equipment_id).first()
    if not eq:
        raise HTTPException(status_code=404, detail="unknown equipment_id")

    if payload.production_log_id is not None:
        log = db.query(models.ProductionLog).filter_by(log_id=payload.production_log_id).first()
        if not log:
            raise HTTPException(status_code=404, detail="unknown production_log_id")
        if log.equipment_id != payload.equipment_id:
            raise HTTPException(
                status_code=400,
                detail="production_log_id belongs to a different equipment_id",
            )

    event = models.QualityEvent(**payload.model_dump())
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@app.get("/quality/defect-summary", response_model=List[schemas.DefectSummary])
def get_defect_summary(
    equipment_ids: Optional[List[str]] = Query(
        None, description="비워두면 등록된 모든 설비를 대상으로 함"
    ),
    hours: int = 24,
    db: Session = Depends(get_db),
):
    """설비별·불량유형별 불량 집계.

    quality_event.production_log_id로 production_log와 연결하여,
    불량유형(defect_type)별 이벤트 건수와 실제 불량 수량(qty_defect) 합을 함께 보여준다.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    return calculate_defect_summary(db, equipment_ids, start, end)


# ---------- 집계 (가동률/생산실적 조회) ----------

@app.get("/equipment/{equipment_id}/oee", response_model=schemas.OeeResponse)
def get_oee(
    equipment_id: str,
    hours: int = 24,
    db: Session = Depends(get_db),
):
    """최근 N시간(기본 24시간) 동안의 가동률(OEE) 계산"""
    eq = db.query(models.Equipment).filter_by(equipment_id=equipment_id).first()
    if not eq:
        raise HTTPException(status_code=404, detail="unknown equipment_id")

    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)
    return calculate_oee(db, equipment_id, start, end)


def _resolve_equipment_ids(db: Session, equipment_ids: Optional[List[str]]) -> List[str]:
    """equipment_ids가 없으면(=None) 등록된 전체 설비를 대상으로 한다.
    현장에서 '설비 하나하나 따로 뽑아야 했던' 불편함을 없애는 핵심 지점.
    """
    if equipment_ids:
        return equipment_ids
    return [row.equipment_id for row in db.query(models.Equipment.equipment_id).all()]


@app.get("/oee", response_model=List[schemas.OeeResponse])
def get_oee_bulk(
    equipment_ids: Optional[List[str]] = Query(
        None, description="비워두면 등록된 모든 설비를 대상으로 함 (?equipment_ids=EQ-001&equipment_ids=EQ-002)"
    ),
    hours: int = 24,
    db: Session = Depends(get_db),
):
    """여러 설비(또는 전체 설비)의 가동률을 한 번에 조회.
    기존에는 설비마다 화면을 따로 열어 뽑아야 했던 것을 한 번의 호출로 해결.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)

    targets = _resolve_equipment_ids(db, equipment_ids)
    if not targets:
        raise HTTPException(status_code=404, detail="no equipment registered")

    return [calculate_oee(db, eq_id, start, end) for eq_id in targets]


# ---------- 이상탐지 결과 조회 (anomaly-worker가 기록) ----------

@app.get("/anomalies", response_model=List[schemas.AnomalySummary])
def get_anomalies(
    equipment_ids: Optional[List[str]] = Query(
        None, description="비워두면 등록된 모든 설비를 대상으로 함 (?equipment_ids=EQ-001&equipment_ids=EQ-002)"
    ),
    hours: int = 24,
    db: Session = Depends(get_db),
):
    """여러 설비(또는 전체 설비)의 최근 N시간 이상탐지 결과를 한 번에 조회 (/oee와 같은 방식)"""
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)

    targets = _resolve_equipment_ids(db, equipment_ids)
    if not targets:
        raise HTTPException(status_code=404, detail="no equipment registered")

    return summarize_anomalies(db, targets, start, end)


@app.get("/drift-alarms", response_model=List[schemas.DriftAlarmOut])
def get_drift_alarms(
    equipment_ids: Optional[List[str]] = Query(
        None, description="비워두면 등록된 모든 설비를 대상으로 함 (?equipment_ids=EQ-001&equipment_ids=EQ-002)"
    ),
    hours: int = 24,
    format: str = Query("json", pattern="^(json|csv)$", description="csv면 바로 내려받기"),
    db: Session = Depends(get_db),
):
    """최근 N시간과 겹치는 열화 경보 이력 (anomaly-worker가 drift_alarm에 기록).
    진행 중인 경보는 cleared_ts가 null, active가 true.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(hours=hours)

    targets = _resolve_equipment_ids(db, equipment_ids)
    if not targets:
        raise HTTPException(status_code=404, detail="no equipment registered")

    alarms = list_drift_alarms(db, targets, start, end)
    if format == "json":
        return alarms

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(DRIFT_ALARM_CSV_COLUMNS)
    for a in alarms:
        writer.writerow([
            v.isoformat() if isinstance(v, datetime) else ("" if v is None else v)
            for v in (a[c] for c in DRIFT_ALARM_CSV_COLUMNS)
        ])
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=drift_alarms_{start.date()}_{end.date()}.csv"},
    )


# ---------- 자연어 질의 (LLM function calling) ----------

NLQ_MAX_ROUNDS = int(os.getenv("NLQ_MAX_ROUNDS", "3"))
NLQ_TIMEOUT_SEC = float(os.getenv("NLQ_TIMEOUT_SEC", "60"))


def get_nlq_provider(requested: Optional[str]):
    """요청의 provider > 환경변수 NLQ_PROVIDER > 키가 있는 제공자(claude, gemini 순).
    키가 없으면 503 - 자연어 질의만 비활성이고 다른 API는 영향 없음."""
    keys = {
        "claude": os.getenv("ANTHROPIC_API_KEY", "").strip(),
        "gemini": os.getenv("GEMINI_API_KEY", "").strip(),
    }
    name = requested or os.getenv("NLQ_PROVIDER", "").strip().lower() or next((p for p in keys if keys[p]), None)
    if name is None:
        raise HTTPException(status_code=503, detail="자연어 질의 비활성: ANTHROPIC_API_KEY 또는 GEMINI_API_KEY를 설정하세요")
    if name not in keys:
        raise HTTPException(status_code=400, detail=f"알 수 없는 provider: {name} (claude 또는 gemini)")
    if not keys[name]:
        raise HTTPException(status_code=503, detail=f"자연어 질의 비활성: {name} API 키가 설정되지 않았습니다")
    if name == "claude":
        return ClaudeProvider(keys[name], os.getenv("CLAUDE_MODEL") or "claude-opus-5", NLQ_TIMEOUT_SEC)
    return GeminiProvider(keys[name], os.getenv("GEMINI_MODEL") or "gemini-flash-latest", NLQ_TIMEOUT_SEC)


@app.post("/query", response_model=schemas.QueryOut)
def natural_language_query(payload: schemas.QueryIn, db: Session = Depends(get_db)):
    """자연어 질문 -> LLM이 읽기 전용 도구(설비 목록, OEE, 불량 집계, 이상탐지)를 골라 호출 -> 답변.
    응답에 호출한 도구·인자·결과를 함께 담아 답변의 근거를 확인할 수 있게 한다.
    """
    provider = get_nlq_provider(payload.provider)

    def run_tool(name: str, args: dict):
        label = name if name in TOOL_NAMES else "unknown"
        try:
            result = execute_tool(db, name, args)
        except ToolError as e:
            NLQ_TOOL_CALLS.labels(provider.name, label, "false").inc()
            return False, str(e)
        NLQ_TOOL_CALLS.labels(provider.name, label, "true").inc()
        return True, result

    try:
        result = provider.run(user_message(payload.question), run_tool, NLQ_MAX_ROUNDS)
    except NLQProviderError as e:
        NLQ_REQUESTS.labels(provider.name, "error").inc()
        raise HTTPException(status_code=502, detail=str(e))
    NLQ_REQUESTS.labels(provider.name, result.stop).inc()
    return asdict(result)


# ---------- 일일 리포트 (예약 리포트) ----------

@app.post("/reports/daily", response_model=List[schemas.DailyReportOut])
def create_daily_report(
    report_date: Optional[date] = Query(None, alias="date", description="KST 날짜 (생략 시 어제). 끝난 날짜만"),
    db: Session = Depends(get_db),
):
    """전체 설비의 하루(KST) 요약을 계산해 daily_report에 저장 (같은 날짜는 덮어씀).
    Helm 차트의 CronJob이 매일 새벽 인자 없이 호출해 전날 리포트를 만든다.
    """
    yesterday = yesterday_kst()
    report_date = report_date or yesterday
    if report_date > yesterday:
        raise HTTPException(status_code=400, detail=f"아직 끝나지 않은 날짜입니다 (생성 가능한 마지막 날짜: {yesterday})")

    targets = _resolve_equipment_ids(db, None)
    if not targets:
        raise HTTPException(status_code=404, detail="no equipment registered")
    return save_daily_report(db, build_daily_report(db, report_date, targets))


@app.get("/reports/daily", response_model=List[schemas.DailyReportOut])
def get_daily_reports(
    equipment_ids: Optional[List[str]] = Query(
        None, description="비워두면 등록된 모든 설비를 대상으로 함 (?equipment_ids=EQ-001&equipment_ids=EQ-002)"
    ),
    start_date: Optional[date] = Query(None, description="생략 시 end_date 6일 전 (최근 7일)"),
    end_date: Optional[date] = Query(None, description="생략 시 어제 (KST)"),
    format: str = Query("json", pattern="^(json|csv)$", description="csv면 바로 내려받기"),
    db: Session = Depends(get_db),
):
    """저장된 일일 리포트 조회 (생성된 날짜만 나옴). 여러 날짜·여러 설비를 한 번에"""
    end_date = end_date or yesterday_kst()
    start_date = start_date or (end_date - timedelta(days=6))
    if start_date > end_date:
        raise HTTPException(status_code=400, detail="start_date가 end_date보다 늦습니다")

    reports = load_daily_reports(db, start_date, end_date, _resolve_equipment_ids(db, equipment_ids))
    if format == "json":
        return reports

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(REPORT_COLUMNS)
    for r in reports:
        writer.writerow([
            v.isoformat() if isinstance(v, (date, datetime)) else ("" if v is None else v)
            for v in (getattr(r, c) for c in REPORT_COLUMNS)
        ])
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename=daily_report_{start_date}_{end_date}.csv"},
    )


@app.get("/export/production-logs")
def export_production_logs(
    equipment_ids: Optional[List[str]] = Query(
        None, description="비워두면 전체 설비 대상"
    ),
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    db: Session = Depends(get_db),
):
    """조건(기간·설비, 여러 개 동시 지정 가능)을 넘기면 바로 CSV로 내려주는
    셀프서비스 익스포트. 화면에서 조건 걸고 다운로드 -> 엑셀 재가공하던
    과정을 API 호출 한 번으로 대체한다.
    """
    end = end or datetime.now(timezone.utc)
    start = start or (end - timedelta(days=1))

    targets = _resolve_equipment_ids(db, equipment_ids)

    rows = (
        db.query(models.ProductionLog)
        .filter(
            models.ProductionLog.equipment_id.in_(targets),
            models.ProductionLog.ts >= start,
            models.ProductionLog.ts <= end,
        )
        .order_by(models.ProductionLog.equipment_id, models.ProductionLog.ts)
        .all()
    )

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["equipment_id", "ts", "qty_good", "qty_defect", "cycle_time_sec", "planned_time_sec"]
    )
    for r in rows:
        writer.writerow(
            [r.equipment_id, r.ts.isoformat(), r.qty_good, r.qty_defect, r.cycle_time_sec, r.planned_time_sec]
        )
    buffer.seek(0)

    filename = f"production_logs_{start.date()}_{end.date()}.csv"
    return StreamingResponse(
        buffer,
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
