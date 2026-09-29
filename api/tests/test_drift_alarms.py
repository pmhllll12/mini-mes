import csv
import io
from datetime import datetime, timedelta, timezone

import models
from database import SessionLocal


def _add(*alarms):
    db = SessionLocal()
    try:
        db.add_all(alarms)
        db.commit()
    finally:
        db.close()


def test_drift_alarms_all_equipment_by_default(client):
    res = client.get("/drift-alarms?hours=1")
    assert res.status_code == 200
    assert isinstance(res.json(), list)


def test_drift_alarms_returns_cleared_and_active(client):
    now = datetime.now(timezone.utc)
    raised = now - timedelta(minutes=50)
    _add(
        models.DriftAlarm(equipment_id="EQ-003", raised_ts=raised, raised_score=1.5, threshold=1.0,
                          cleared_ts=raised + timedelta(minutes=10)),
        # 기간(최근 1시간) 이전에 시작했지만 기간 안에 해제된 경보도 포함
        models.DriftAlarm(equipment_id="EQ-003", raised_ts=now - timedelta(minutes=90), raised_score=1.2,
                          threshold=1.0, cleared_ts=now - timedelta(minutes=55)),
        # 기간 이전에 끝난 경보는 제외
        models.DriftAlarm(equipment_id="EQ-003", raised_ts=now - timedelta(hours=5), raised_score=1.1,
                          threshold=1.0, cleared_ts=now - timedelta(hours=4)),
    )

    rows = [r for r in client.get("/drift-alarms?equipment_ids=EQ-003&hours=1").json()
            if r["raised_score"] in (1.5, 1.2, 1.1)]
    assert [r["raised_score"] for r in rows] == [1.2, 1.5]
    closed = rows[1]
    assert closed["active"] is False and closed["duration_sec"] == 600


def test_drift_alarms_active_alarm_has_no_clear_time(client):
    db = SessionLocal()
    try:
        has_open = db.query(models.DriftAlarm).filter_by(equipment_id="EQ-001", cleared_ts=None).count()
    finally:
        db.close()
    if not has_open:  # 워커가 이미 진행 중 경보를 가진 설비면 유일 인덱스와 충돌하므로 새로 만들지 않는다
        _add(models.DriftAlarm(equipment_id="EQ-001", raised_ts=datetime.now(timezone.utc) - timedelta(minutes=5),
                               raised_score=2.0, threshold=1.0))

    rows = client.get("/drift-alarms?equipment_ids=EQ-001&hours=1").json()
    active = [r for r in rows if r["active"]]
    assert len(active) == 1
    assert active[0]["cleared_ts"] is None and active[0]["duration_sec"] is None


def test_drift_alarms_csv(client):
    now = datetime.now(timezone.utc)
    _add(models.DriftAlarm(equipment_id="EQ-002", raised_ts=now - timedelta(minutes=30), raised_score=1.75,
                           threshold=1.0, cleared_ts=now - timedelta(minutes=20)))

    res = client.get("/drift-alarms?equipment_ids=EQ-002&hours=1&format=csv")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(res.text)))
    row = next(r for r in rows if r["raised_score"] == "1.75")
    assert row["active"] == "False" and row["duration_sec"] == "600.0"


def test_drift_alarms_rejects_unknown_format(client):
    assert client.get("/drift-alarms?format=xml").status_code == 422
