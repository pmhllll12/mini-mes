from datetime import datetime, timedelta, timezone

import models
from database import SessionLocal


def test_anomalies_all_equipment_by_default(client):
    res = client.get("/anomalies?hours=1")
    assert res.status_code == 200
    ids = {row["equipment_id"] for row in res.json()}
    assert {"EQ-001", "EQ-002", "EQ-003"} <= ids


def test_anomalies_multiple_equipment_ids(client):
    res = client.get("/anomalies?equipment_ids=EQ-001&equipment_ids=EQ-003&hours=1")
    assert res.status_code == 200
    assert [row["equipment_id"] for row in res.json()] == ["EQ-001", "EQ-003"]


def test_anomalies_counts_recorded_result(client):
    ts = datetime.now(timezone.utc) - timedelta(minutes=1)
    db = SessionLocal()
    try:
        db.add(models.AnomalyResult(equipment_id="EQ-002", ts=ts, anomaly_score=0.9876, is_anomaly=True))
        db.commit()
    finally:
        db.close()

    row = client.get("/anomalies?equipment_ids=EQ-002&hours=1").json()[0]
    assert row["anomaly_count"] >= 1
    assert row["max_score"] >= 0.9876
    assert any(a["anomaly_score"] == 0.9876 for a in row["anomalies"])
