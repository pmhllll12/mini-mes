from datetime import datetime, timedelta, timezone

import models
from database import SessionLocal
from oee import calculate_oee


def test_availability_capped_at_one():
    """cycle_time * 생산수량이 planned_time을 훨씬 초과해도 가동률(availability)은 1.0을 넘지 않아야 한다."""
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        db.add(
            models.ProductionLog(
                equipment_id="EQ-001",
                ts=now,
                qty_good=100,
                qty_defect=0,
                cycle_time_sec=1000.0,  # run_time(=cycle_time*수량)이 planned_time을 크게 초과하도록
                planned_time_sec=60.0,
            )
        )
        db.commit()

        result = calculate_oee(db, "EQ-001", now - timedelta(minutes=1), now + timedelta(minutes=1))

        assert result["availability"] <= 1.0
        assert result["oee"] <= 1.0
    finally:
        db.close()
