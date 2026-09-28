"""
테스트는 실제 운영과 동일한 Postgres를 사용한다 (DATABASE_URL로 지정, 기본값은 database.py와 동일).
DB에는 db/schema.sql이 미리 적용되어 있어야 한다 (설비 시드 EQ-001~003 포함).
"""
import pytest
from fastapi.testclient import TestClient

from main import app


@pytest.fixture()
def client():
    return TestClient(app)
