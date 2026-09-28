"""
품질 이벤트 집계 로직

quality_event.production_log_id로 production_log와 연결하여,
설비별·불량유형별로 이벤트 건수와 해당 생산실적의 불량 수량 합을 집계한다.
production_log와 연결되지 않은 이벤트는 total_qty_defect에 포함되지 않는다.
"""
from datetime import datetime
from typing import List, Optional
from sqlalchemy import func
from sqlalchemy.orm import Session

from models import ProductionLog, QualityEvent


def calculate_defect_summary(
    db: Session,
    equipment_ids: Optional[List[str]],
    start: datetime,
    end: datetime,
) -> List[dict]:
    query = (
        db.query(
            QualityEvent.equipment_id.label("equipment_id"),
            QualityEvent.defect_type.label("defect_type"),
            func.count(QualityEvent.event_id).label("event_count"),
            func.coalesce(func.sum(ProductionLog.qty_defect), 0).label("total_qty_defect"),
        )
        .outerjoin(ProductionLog, QualityEvent.production_log_id == ProductionLog.log_id)
        .filter(QualityEvent.ts >= start, QualityEvent.ts <= end)
    )
    if equipment_ids:
        query = query.filter(QualityEvent.equipment_id.in_(equipment_ids))

    rows = (
        query.group_by(QualityEvent.equipment_id, QualityEvent.defect_type)
        .order_by(QualityEvent.equipment_id, QualityEvent.defect_type)
        .all()
    )

    return [
        {
            "equipment_id": r.equipment_id,
            "defect_type": r.defect_type,
            "event_count": int(r.event_count),
            "total_qty_defect": int(r.total_qty_defect),
        }
        for r in rows
    ]
