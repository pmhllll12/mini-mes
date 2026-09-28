import csv
import io
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db, engine, Base
import models
import schemas
from oee import calculate_oee
from quality import calculate_defect_summary

# 로컬 개발 편의를 위해 앱 시작 시 테이블 자동 생성
# (운영에서는 schema.sql / 마이그레이션 도구를 통해 관리)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="mini-mes", version="0.1.0")

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
