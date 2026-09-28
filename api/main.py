import csv
import io
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db, engine, Base
import models
import schemas
from oee import calculate_oee

# 로컬 개발 편의를 위해 앱 시작 시 테이블 자동 생성
# (운영에서는 schema.sql / 마이그레이션 도구를 통해 관리)
Base.metadata.create_all(bind=engine)

app = FastAPI(title="mini-mes", version="0.1.0")


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- 설비 ----------

@app.get("/equipment", response_model=List[schemas.EquipmentStatus])
def list_equipment(db: Session = Depends(get_db)):
    return db.query(models.Equipment).all()


@app.get("/equipment/{equipment_id}", response_model=schemas.EquipmentStatus)
def get_equipment(equipment_id: str, db: Session = Depends(get_db)):
    eq = db.query(models.Equipment).filter_by(equipment_id=equipment_id).first()
    if not eq:
        raise HTTPException(status_code=404, detail="equipment not found")
    return eq


# ---------- 생산실적 수집 (설비/시뮬레이터가 호출) ----------

@app.post("/production-logs", response_model=schemas.ProductionLogOut, status_code=201)
def create_production_log(payload: schemas.ProductionLogIn, db: Session = Depends(get_db)):
    eq = db.query(models.Equipment).filter_by(equipment_id=payload.equipment_id).first()
    if not eq:
        raise HTTPException(status_code=404, detail="unknown equipment_id")

    log = models.ProductionLog(**payload.model_dump())
    db.add(log)
    db.commit()
    db.refresh(log)
    return log


# ---------- 품질 이벤트 수집 ----------

@app.post("/quality-events", response_model=schemas.QualityEventOut, status_code=201)
def create_quality_event(payload: schemas.QualityEventIn, db: Session = Depends(get_db)):
    eq = db.query(models.Equipment).filter_by(equipment_id=payload.equipment_id).first()
    if not eq:
        raise HTTPException(status_code=404, detail="unknown equipment_id")

    event = models.QualityEvent(**payload.model_dump())
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


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
