from sqlalchemy import (
    Column, String, Integer, Numeric, Boolean, Date, TIMESTAMP, BigInteger, ForeignKey, text
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


class DailyReport(Base):
    __tablename__ = "daily_report"

    report_date = Column(Date, primary_key=True)
    equipment_id = Column(String(20), ForeignKey("equipment.equipment_id"), primary_key=True)
    availability = Column(Numeric(6, 4), nullable=False)
    quality_rate = Column(Numeric(6, 4), nullable=False)
    oee = Column(Numeric(6, 4), nullable=False)
    total_qty = Column(Integer, nullable=False)
    total_defect = Column(Integer, nullable=False)
    top_defect_type = Column(String(50))
    top_defect_events = Column(Integer, nullable=False)
    top_defect_qty = Column(Integer, nullable=False)
    anomaly_scored = Column(Integer, nullable=False)
    anomaly_count = Column(Integer, nullable=False)
    drift_alarms = Column(Integer, nullable=False)
    drift_alarm_sec = Column(Numeric(12, 1), nullable=False)
    generated_at = Column(TIMESTAMP(timezone=True), server_default=text("now()"), nullable=False)


class DriftAlarm(Base):
    __tablename__ = "drift_alarm"

    alarm_id = Column(BigInteger, primary_key=True, autoincrement=True)
    equipment_id = Column(String(20), ForeignKey("equipment.equipment_id"), nullable=False)
    raised_ts = Column(TIMESTAMP(timezone=True), nullable=False)
    raised_score = Column(Numeric(10, 4), nullable=False)
    threshold = Column(Numeric(10, 4), nullable=False)
    cleared_ts = Column(TIMESTAMP(timezone=True))
