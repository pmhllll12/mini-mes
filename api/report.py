"""
일일 리포트 (예약 리포트)

KST 하루(00:00 ~ 다음 날 00:00) 동안의 설비별 OEE·불량·이상·열화 경보를 한 줄로 요약해
daily_report 테이블에 스냅샷으로 저장한다. 원본 데이터가 나중에 바뀌거나 지워져도 그날 보고한 값이 남는다.
같은 날짜를 다시 생성하면 덮어쓴다 (늦게 들어온 데이터 반영).
Helm 차트의 CronJob이 매일 새벽 전날 리포트를 생성한다 (POST /reports/daily).
"""
from datetime import date, datetime, time, timedelta, timezone
from typing import List

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from anomaly import list_drift_alarms, summarize_anomalies
from models import DailyReport
from oee import calculate_oee
from quality import calculate_defect_summary

KST = timezone(timedelta(hours=9), "KST")  # 한국은 서머타임이 없어 고정 오프셋으로 충분

REPORT_COLUMNS = [
    "report_date", "equipment_id", "availability", "quality_rate", "oee", "total_qty", "total_defect",
    "top_defect_type", "top_defect_events", "top_defect_qty", "anomaly_scored", "anomaly_count",
    "drift_alarms", "drift_alarm_sec", "generated_at",
]


def yesterday_kst(now: datetime = None) -> date:
    return ((now or datetime.now(timezone.utc)).astimezone(KST) - timedelta(days=1)).date()


def day_window(report_date: date) -> tuple[datetime, datetime]:
    """KST 하루. 기존 집계 함수가 끝을 포함(<=)하므로 다음 날 00:00 직전까지로 잘라 이틀에 겹쳐 잡히지 않게 한다"""
    start = datetime.combine(report_date, time.min, KST)
    return start, start + timedelta(days=1) - timedelta(microseconds=1)


def build_daily_report(db: Session, report_date: date, equipment_ids: List[str], now: datetime = None) -> List[dict]:
    start, end = day_window(report_date)
    now = now or datetime.now(timezone.utc)

    defects = {}
    for r in calculate_defect_summary(db, equipment_ids, start, end):
        best = defects.get(r["equipment_id"])
        # 최다 불량 유형: 이벤트 건수 우선, 같으면 불량 수량
        if best is None or (r["event_count"], r["total_qty_defect"]) > (best["event_count"], best["total_qty_defect"]):
            defects[r["equipment_id"]] = r
    anomalies = {a["equipment_id"]: a for a in summarize_anomalies(db, equipment_ids, start, end)}
    drift = {eq_id: [] for eq_id in equipment_ids}
    for a in list_drift_alarms(db, equipment_ids, start, end):
        drift[a["equipment_id"]].append(a)

    rows = []
    for eq_id in equipment_ids:
        oee = calculate_oee(db, eq_id, start, end)
        top = defects.get(eq_id)
        alarms = drift[eq_id]
        # 경보 시간은 하루 구간으로 잘라 합산 (진행 중이면 지금까지)
        alarm_sec = sum(
            max((min(a["cleared_ts"] or now, end) - max(a["raised_ts"], start)).total_seconds(), 0) for a in alarms
        )
        rows.append({
            "report_date": report_date,
            "equipment_id": eq_id,
            "availability": oee["availability"],
            "quality_rate": oee["quality_rate"],
            "oee": oee["oee"],
            "total_qty": oee["total_qty"],
            "total_defect": oee["total_defect"],
            "top_defect_type": top["defect_type"] if top else None,
            "top_defect_events": top["event_count"] if top else 0,
            "top_defect_qty": top["total_qty_defect"] if top else 0,
            "anomaly_scored": anomalies[eq_id]["scored_count"],
            "anomaly_count": anomalies[eq_id]["anomaly_count"],
            "drift_alarms": sum(1 for a in alarms if a["raised_ts"] >= start),  # 이날 시작한 경보
            "drift_alarm_sec": round(alarm_sec, 1),
        })
    return rows


def save_daily_report(db: Session, rows: List[dict]) -> List[DailyReport]:
    """(report_date, equipment_id)로 덮어쓴다"""
    for row in rows:
        stmt = insert(DailyReport).values(**row)
        db.execute(stmt.on_conflict_do_update(
            index_elements=[DailyReport.report_date, DailyReport.equipment_id],
            set_={**{k: stmt.excluded[k] for k in row if k not in ("report_date", "equipment_id")},
                  "generated_at": datetime.now(timezone.utc)},
        ))
    db.commit()
    if not rows:
        return []
    return load_daily_reports(db, rows[0]["report_date"], rows[0]["report_date"], [r["equipment_id"] for r in rows])


def load_daily_reports(db: Session, start_date: date, end_date: date, equipment_ids: List[str]) -> List[DailyReport]:
    return (
        db.query(DailyReport)
        .filter(
            DailyReport.report_date >= start_date,
            DailyReport.report_date <= end_date,
            DailyReport.equipment_id.in_(equipment_ids),
        )
        .order_by(DailyReport.report_date, DailyReport.equipment_id)
        .all()
    )
