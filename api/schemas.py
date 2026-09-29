from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, ConfigDict, Field


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
    production_log_id: Optional[int] = None  # 이 불량이 발생한 생산실적 건 (선택)
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


class DefectSummary(BaseModel):
    """설비별·불량유형별 불량 집계 응답"""
    equipment_id: str
    defect_type: str
    event_count: int        # 해당 설비/불량유형의 품질 이벤트 건수
    total_qty_defect: int   # production_log에 연결된 이벤트들의 불량 수량 합


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


class AnomalyPoint(BaseModel):
    ts: datetime              # 판정 대상 생산실적의 ts
    anomaly_score: float      # 클수록 이상 (Isolation Forest)


class AnomalySummary(BaseModel):
    """설비별 이상탐지 결과 응답 (anomaly_result 기준)"""
    equipment_id: str
    period_start: datetime
    period_end: datetime
    scored_count: int             # 기간 내 판정된 생산실적 건수
    anomaly_count: int            # 그중 is_anomaly = true 건수
    max_score: Optional[float]    # 기간 내 최대 이상 점수 (판정 0건이면 null)
    last_scored_ts: Optional[datetime]
    anomalies: List[AnomalyPoint]  # is_anomaly = true 인 건 (최신순)


class DailyReportOut(BaseModel):
    """일일 리포트 1행 (설비 1대 x KST 하루, daily_report 스냅샷)"""
    model_config = ConfigDict(from_attributes=True)

    report_date: date
    equipment_id: str
    availability: float
    quality_rate: float
    oee: float
    total_qty: int
    total_defect: int
    top_defect_type: Optional[str]  # 이벤트 건수 최다 불량 유형 (없으면 null)
    top_defect_events: int          # 그 유형의 품질 이벤트 건수
    top_defect_qty: int             # 그 유형 이벤트에 연결된 불량 수량(개)
    anomaly_scored: int
    anomaly_count: int
    drift_alarms: int               # 이날 시작한 열화 경보 수
    drift_alarm_sec: float          # 이날 열화 경보가 켜져 있던 시간 합(초)
    generated_at: datetime


class DriftAlarmOut(BaseModel):
    """열화 경보 1건 (drift_alarm 기준). ts는 경보를 켜고/끈 판정 대상 생산실적의 ts"""
    equipment_id: str
    raised_ts: datetime
    cleared_ts: Optional[datetime]  # null이면 경보 중
    active: bool
    duration_sec: Optional[float]   # 해제된 경보만 (raised_ts ~ cleared_ts)
    raised_score: float             # 경보를 켠 구간의 열화 점수
    threshold: float


class QueryIn(BaseModel):
    """자연어 질의 요청"""
    question: str = Field(min_length=1, max_length=500)
    provider: Optional[Literal["claude", "gemini", "openai_compat"]] = None  # 생략 시 NLQ_PROVIDER 또는 설정된 제공자


class QueryToolCall(BaseModel):
    name: str
    input: Dict[str, Any]
    ok: bool
    result: Optional[Any] = None   # 도구가 돌려준 데이터 (답변의 근거)
    error: Optional[str] = None


class QueryOut(BaseModel):
    """자연어 질의 응답 - 답변과 함께 호출한 도구·인자·결과를 돌려줘 근거를 확인할 수 있게 한다"""
    provider: str
    model: str
    answer: str
    stop: str                      # answer | max_rounds | max_tokens | refusal
    tool_calls: List[QueryToolCall]
