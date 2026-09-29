import csv
import io
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import text

import models
from database import SessionLocal, engine
from report import KST, yesterday_kst

DAY = date(2020, 1, 15)  # 다른 데이터가 없는 과거 날짜


def _kst(day_offset, hour, minute=0):
    return datetime(2020, 1, 15, hour, minute, tzinfo=KST) + timedelta(days=day_offset)


@pytest.fixture()
def seeded_day():
    """EQ-001에 2020-01-15(KST) 하루치 데이터. 자정 경계·날짜를 넘는 경보 포함"""
    db = SessionLocal()
    try:
        log1 = models.ProductionLog(equipment_id="EQ-001", ts=_kst(0, 10), qty_good=8, qty_defect=2,
                                    cycle_time_sec=10, planned_time_sec=120)
        log2 = models.ProductionLog(equipment_id="EQ-001", ts=_kst(0, 23), qty_good=10, qty_defect=0,
                                    cycle_time_sec=10, planned_time_sec=120)
        # 다음 날 00:00 KST 정각 - 다음 날 리포트에 속함
        midnight = models.ProductionLog(equipment_id="EQ-001", ts=_kst(1, 0), qty_good=5, qty_defect=5,
                                        cycle_time_sec=10, planned_time_sec=120)
        db.add_all([log1, log2, midnight])
        db.flush()
        db.add_all([
            models.QualityEvent(equipment_id="EQ-001", ts=_kst(0, 10), defect_type="scratch", production_log_id=log1.log_id),
            models.QualityEvent(equipment_id="EQ-001", ts=_kst(0, 10, 1), defect_type="scratch", production_log_id=log1.log_id),
            models.QualityEvent(equipment_id="EQ-001", ts=_kst(0, 10, 2), defect_type="burr", production_log_id=log1.log_id),
            models.AnomalyResult(equipment_id="EQ-001", ts=_kst(0, 10), anomaly_score=0.5, is_anomaly=False),
            models.AnomalyResult(equipment_id="EQ-001", ts=_kst(0, 23), anomaly_score=2.5, is_anomaly=True),
            # 이날 22:00 시작 ~ 다음 날 01:00 해제 -> 이날 몫 2시간
            models.DriftAlarm(equipment_id="EQ-001", raised_ts=_kst(0, 22), raised_score=1.5, threshold=1.0,
                              cleared_ts=_kst(1, 1)),
            # 전날 23:30 시작 ~ 이날 00:30 해제 -> 이날 몫 30분, 이날 시작한 경보 수에는 안 셈
            models.DriftAlarm(equipment_id="EQ-001", raised_ts=_kst(-1, 23, 30), raised_score=1.2, threshold=1.0,
                              cleared_ts=_kst(0, 0, 30)),
        ])
        db.commit()
    finally:
        db.close()
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM daily_report WHERE report_date = :d"), {"d": DAY})


def _eq(rows, equipment_id):
    return next(r for r in rows if r["equipment_id"] == equipment_id)


def test_daily_report_summarizes_one_kst_day(client, seeded_day):
    res = client.post(f"/reports/daily?date={DAY}")
    assert res.status_code == 200
    row = _eq(res.json(), "EQ-001")
    # 실행 200초 / 계획 240초, 양품 18 / 20 (자정 정각 로그 제외)
    assert (row["availability"], row["quality_rate"], row["oee"]) == (0.8333, 0.9, 0.75)
    assert (row["total_qty"], row["total_defect"]) == (20, 2)
    # 최다 불량: scratch 이벤트 2건, 연결된 불량 수량 2+2개
    assert (row["top_defect_type"], row["top_defect_events"], row["top_defect_qty"]) == ("scratch", 2, 4)
    assert (row["anomaly_scored"], row["anomaly_count"]) == (2, 1)
    assert row["drift_alarms"] == 1
    assert row["drift_alarm_sec"] == 9000.0  # 2시간 + 30분

    # 데이터 없는 설비도 한 줄 (0과 null)
    empty = _eq(res.json(), "EQ-002")
    assert (empty["total_qty"], empty["top_defect_type"], empty["drift_alarms"]) == (0, None, 0)


def test_daily_report_regenerate_overwrites(client, seeded_day):
    first = _eq(client.post(f"/reports/daily?date={DAY}").json(), "EQ-001")
    db = SessionLocal()
    try:  # 늦게 들어온 데이터
        db.add(models.ProductionLog(equipment_id="EQ-001", ts=_kst(0, 12), qty_good=0, qty_defect=10,
                                    cycle_time_sec=10, planned_time_sec=120))
        db.commit()
    finally:
        db.close()
    second = _eq(client.post(f"/reports/daily?date={DAY}").json(), "EQ-001")
    assert second["total_defect"] == first["total_defect"] + 10
    assert second["generated_at"] >= first["generated_at"]

    stored = client.get(f"/reports/daily?start_date={DAY}&end_date={DAY}&equipment_ids=EQ-001").json()
    assert len(stored) == 1 and stored[0]["total_defect"] == second["total_defect"]


def test_daily_report_csv(client, seeded_day):
    client.post(f"/reports/daily?date={DAY}")
    res = client.get(f"/reports/daily?start_date={DAY}&end_date={DAY}&format=csv")
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(res.text)))
    assert [r["equipment_id"] for r in rows] == ["EQ-001", "EQ-002", "EQ-003"]
    assert rows[0]["report_date"] == "2020-01-15" and rows[0]["oee"] == "0.7500"
    assert rows[1]["top_defect_type"] == ""


def test_daily_report_rejects_unfinished_day(client):
    today = yesterday_kst() + timedelta(days=1)
    res = client.post(f"/reports/daily?date={today}")
    assert res.status_code == 400


def test_daily_report_rejects_reversed_range(client):
    assert client.get("/reports/daily?start_date=2020-01-16&end_date=2020-01-15").status_code == 400
