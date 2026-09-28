from datetime import datetime
from typing import Optional
from pydantic import BaseModel, ConfigDict


class ProductionLogIn(BaseModel):
    """설비 시뮬레이터/센서가 보내는 생산실적 데이터"""
    equipment_id: str
    qty_good: int = 0
    qty_defect: int = 0
    cycle_time_sec: Optional[float] = None
    planned_time_sec: Optional[float] = None


class ProductionLogOut(ProductionLogIn):
    model_config = ConfigDict(from_attributes=True)

    log_id: int
    ts: datetime


class QualityEventIn(BaseModel):
    equipment_id: str
    defect_type: str
    severity: str = "low"
    note: Optional[str] = None


class QualityEventOut(QualityEventIn):
    model_config = ConfigDict(from_attributes=True)

    event_id: int
    ts: datetime


class EquipmentStatus(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    equipment_id: str
    name: str
    line_id: str
    status: str


class OeeResponse(BaseModel):
    """가동률(OEE) 집계 응답"""
    equipment_id: str
    period_start: datetime
    period_end: datetime
    availability: float   # 가동률 (계획시간 대비 실제가동시간)
    quality_rate: float   # 양품률 (총생산 대비 양품)
    oee: float            # 종합설비효율 = availability * quality_rate
    total_qty: int
    total_defect: int
