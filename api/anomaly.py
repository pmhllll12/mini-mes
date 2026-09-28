"""
이상탐지 결과 조회 로직

anomaly-worker가 anomaly_result에 기록한 결과를 설비별로 요약한다.
(anomaly_result.ts = 판정 대상 생산실적의 ts)
"""
from datetime import datetime
from typing import List

from sqlalchemy import func
from sqlalchemy.orm import Session

from models import AnomalyResult


def summarize_anomalies(db: Session, equipment_ids: List[str], start: datetime, end: datetime) -> List[dict]:
    in_period = (
        AnomalyResult.equipment_id.in_(equipment_ids),
        AnomalyResult.ts >= start,
        AnomalyResult.ts <= end,
    )
    stats = {
        row.equipment_id: row
        for row in db.query(
            AnomalyResult.equipment_id,
            func.count(AnomalyResult.result_id).label("scored_count"),
            func.count(AnomalyResult.result_id).filter(AnomalyResult.is_anomaly.is_(True)).label("anomaly_count"),
            func.max(AnomalyResult.anomaly_score).label("max_score"),
            func.max(AnomalyResult.ts).label("last_scored_ts"),
        )
        .filter(*in_period)
        .group_by(AnomalyResult.equipment_id)
        .all()
    }

    anomalies = {eq_id: [] for eq_id in equipment_ids}
    for r in (
        db.query(AnomalyResult.equipment_id, AnomalyResult.ts, AnomalyResult.anomaly_score)
        .filter(*in_period, AnomalyResult.is_anomaly.is_(True))
        .order_by(AnomalyResult.ts.desc())
        .all()
    ):
        anomalies[r.equipment_id].append({"ts": r.ts, "anomaly_score": float(r.anomaly_score)})

    result = []
    for eq_id in equipment_ids:
        s = stats.get(eq_id)
        result.append({
            "equipment_id": eq_id,
            "period_start": start,
            "period_end": end,
            "scored_count": s.scored_count if s else 0,
            "anomaly_count": s.anomaly_count if s else 0,
            "max_score": float(s.max_score) if s else None,
            "last_scored_ts": s.last_scored_ts if s else None,
            "anomalies": anomalies[eq_id],
        })
    return result
