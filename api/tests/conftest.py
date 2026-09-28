"""
테스트는 실제 운영과 동일한 Postgres를 사용한다 (DATABASE_URL로 지정, 기본값은 database.py와 동일).
DB에는 db/schema.sql이 미리 적용되어 있어야 한다 (설비 시드 EQ-001~003 포함).

로컬 개발 DB를 더럽히지 않도록, 각 테스트가 넣은 행은 테스트가 끝나면 지운다 (cleanup_test_rows).
- SQLAlchemy after_insert 이벤트로 "이 테스트 프로세스가 넣은 행"만 기록한다.
  TestClient는 앱을 같은 프로세스에서 실행하므로 API를 거쳐 들어간 행도 잡힌다.
  동시에 돌고 있는 시뮬레이터·워커(다른 프로세스)가 넣은 행은 건드리지 않는다.
- 워커가 테스트용 생산실적을 판정해 남긴 anomaly_result도 (equipment_id, ts)로 찾아 지운다.
- 생산실적 수신으로 바뀐 설비 status는 테스트 전 값으로 되돌린다.
- KEEP_TEST_DATA=1 이면 지우지 않는다 (디버깅용).
"""
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, text

import models
from database import engine
from main import app

TRACKED_MODELS = {
    models.ProductionLog: "log_ids",
    models.QualityEvent: "event_ids",
    models.AnomalyResult: "anomaly_result_ids",
}


@pytest.fixture(autouse=True)
def cleanup_test_rows():
    created = {key: [] for key in TRACKED_MODELS.values()}
    listeners = []
    for model, key in TRACKED_MODELS.items():
        def _record(mapper, connection, target, key=key):
            created[key].append(mapper.primary_key_from_instance(target)[0])
        event.listen(model, "after_insert", _record)
        listeners.append((model, _record))

    with engine.connect() as conn:
        statuses = dict(conn.execute(text("SELECT equipment_id, status FROM equipment")).all())

    try:
        yield created
    finally:
        for model, fn in listeners:
            event.remove(model, "after_insert", fn)
        if os.getenv("KEEP_TEST_DATA") != "1":
            _delete_created(created, statuses)


def _delete_created(created: dict, statuses: dict) -> None:
    with engine.begin() as conn:
        # FK 순서: quality_event -> production_log. anomaly_result는 FK 없이 (equipment_id, ts)로 연결
        conn.execute(
            text("DELETE FROM quality_event WHERE event_id = ANY(:event_ids) OR production_log_id = ANY(:log_ids)"),
            created,
        )
        conn.execute(text("DELETE FROM anomaly_result WHERE result_id = ANY(:anomaly_result_ids)"), created)
        conn.execute(
            text(
                "DELETE FROM anomaly_result a USING production_log p "
                "WHERE p.log_id = ANY(:log_ids) AND a.equipment_id = p.equipment_id AND a.ts = p.ts"
            ),
            created,
        )
        conn.execute(text("DELETE FROM production_log WHERE log_id = ANY(:log_ids)"), created)
        for equipment_id, status in statuses.items():
            conn.execute(
                text("UPDATE equipment SET status = :status WHERE equipment_id = :equipment_id AND status <> :status"),
                {"equipment_id": equipment_id, "status": status},
            )


@pytest.fixture()
def client():
    return TestClient(app)
