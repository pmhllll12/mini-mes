"""
OEE(종합설비효율) 계산 로직

OEE = 가동률(Availability) x 양품률(Quality Rate)
- 실제 가동시간 = 사이클타임(제품 1개당 초) x 생산 수량(양품+불량)
  (한 구간의 계획시간을 넘을 수 없으므로 행 단위로 planned_time_sec 상한 적용)
- 가동률 = 실제 가동시간 합 / 계획 가동시간 합   (항상 0~1)
- 양품률 = 양품 수 / 총 생산 수(양품+불량)

* 원래 OEE는 여기에 성능가동률(Performance)까지 곱하지만,
  이 프로젝트에서는 가동률 x 양품률로 단순화한다.
"""
from datetime import datetime
from sqlalchemy import func
from sqlalchemy.orm import Session

from models import ProductionLog


def calculate_oee(db: Session, equipment_id: str, start: datetime, end: datetime) -> dict:
    total_produced = ProductionLog.qty_good + ProductionLog.qty_defect
    run_time_per_row = func.least(
        func.coalesce(ProductionLog.cycle_time_sec, 0) * total_produced,
        ProductionLog.planned_time_sec,
    )

    row = (
        db.query(
            func.coalesce(func.sum(ProductionLog.qty_good), 0).label("total_good"),
            func.coalesce(func.sum(ProductionLog.qty_defect), 0).label("total_defect"),
            func.coalesce(func.sum(run_time_per_row), 0).label("run_time"),
            func.coalesce(func.sum(ProductionLog.planned_time_sec), 0).label("planned_time"),
        )
        .filter(
            ProductionLog.equipment_id == equipment_id,
            ProductionLog.ts >= start,
            ProductionLog.ts <= end,
        )
        .one()
    )

    total_good = int(row.total_good)
    total_defect = int(row.total_defect)
    total_qty = total_good + total_defect
    planned_time = float(row.planned_time)
    run_time = float(row.run_time)

    availability = min(run_time / planned_time, 1.0) if planned_time > 0 else 0.0
    quality_rate = (total_good / total_qty) if total_qty > 0 else 0.0
    oee = round(availability * quality_rate, 4)

    return {
        "equipment_id": equipment_id,
        "period_start": start,
        "period_end": end,
        "availability": round(availability, 4),
        "quality_rate": round(quality_rate, 4),
        "oee": oee,
        "total_qty": total_qty,
        "total_defect": total_defect,
    }