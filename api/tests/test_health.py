def test_health(client):
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_metrics_exports_machining_fail_rate(client):
    """재생 적재로 최근 1시간 안에 도착한 정밀가공 건이 /metrics의 건수·불합격률에 잡힌다"""
    import re
    from datetime import datetime, timedelta, timezone

    import models
    from database import SessionLocal
    from machining_quality import SENSOR_COLUMNS

    now = datetime.now(timezone.utc)
    source = datetime(2001, 2, 1, tzinfo=timezone.utc)
    db = SessionLocal()
    try:
        for serial, received, pass_or_fail in [("M-1", now - timedelta(minutes=10), 1),
                                               ("M-2", now + timedelta(minutes=10), 1)]:
            db.add(models.MachiningQualityRecord(serial_no=serial, source_received_at=source, received_at=received,
                                                 pass_or_fail=pass_or_fail, **{c: 1.0 for c in SENSOR_COLUMNS.values()}))
        db.commit()
    finally:
        db.close()

    body = client.get("/metrics").text
    fail = float(re.search(r'^mes_machining_records\{result="fail"\} (\S+)$', body, re.M).group(1))
    rate = float(re.search(r"^mes_machining_fail_rate (\S+)$", body, re.M).group(1))
    assert fail >= 1 and 0 < rate <= 1
