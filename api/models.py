from sqlalchemy import (
    Column, String, Integer, Numeric, Boolean, TIMESTAMP, BigInteger, ForeignKey, text
)
from database import Base


class Equipment(Base):
    __tablename__ = "equipment"

    equipment_id = Column(String(20), primary_key=True)
    name = Column(String(100), nullable=False)
    line_id = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="idle")
    created_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"))


class ProductionLog(Base):
    __tablename__ = "production_log"

    log_id = Column(BigInteger, primary_key=True, autoincrement=True)
    equipment_id = Column(String(20), ForeignKey("equipment.equipment_id"), nullable=False)
    ts = Column(TIMESTAMP(timezone=True), server_default=text("now()"))
    qty_good = Column(Integer, default=0)
    qty_defect = Column(Integer, default=0)
    cycle_time_sec = Column(Numeric(10, 2))
    planned_time_sec = Column(Numeric(10, 2))


class QualityEvent(Base):
    __tablename__ = "quality_event"

    event_id = Column(BigInteger, primary_key=True, autoincrement=True)
    equipment_id = Column(String(20), ForeignKey("equipment.equipment_id"), nullable=False)
    production_log_id = Column(BigInteger, ForeignKey("production_log.log_id"), nullable=True)
    ts = Column(TIMESTAMP(timezone=True), server_default=text("now()"))
    defect_type = Column(String(50), nullable=False)
    severity = Column(String(10), default="low")
    note = Column(String)


class AnomalyResult(Base):
    __tablename__ = "anomaly_result"

    result_id = Column(BigInteger, primary_key=True, autoincrement=True)
    equipment_id = Column(String(20), ForeignKey("equipment.equipment_id"), nullable=False)
    ts = Column(TIMESTAMP(timezone=True), server_default=text("now()"))
    anomaly_score = Column(Numeric(10, 4), nullable=False)
    is_anomaly = Column(Boolean, default=False)
