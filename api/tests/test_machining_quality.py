"""
정밀가공 품질(KAMP) - 적재 스크립트와 조회 API 테스트
- 실제 CSV는 저장소에 없으므로(라이선스) 같은 헤더의 작은 CSV를 만들어 쓴다
- 실제 데이터(2022-08)·다른 테스트와 겹치지 않게 2001-03(적재), 2001-04(API) 시각을 쓴다
- 적재 스크립트는 raw SQL로 넣어 conftest의 after_insert 추적에 잡히지 않으므로, SerialNo 접두어로 직접 지운다
"""
import csv
import io
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

import load_machining_quality as loader
import machining_quality as mq
import models
from database import SessionLocal, engine

LOAD_PREFIX = "LT-2001-"
API_START = datetime(2001, 4, 1, tzinfo=mq.KST)
API_RANGE = {"start": API_START.isoformat(), "end": (API_START + timedelta(days=1)).isoformat()}


def _csv_row(serial: str, received: str, pass_or_fail: int, speed: float = 100.0) -> dict:
    row = {mq.SERIAL_COLUMN: serial, mq.RECEIVED_COLUMN: received, mq.LABEL_COLUMN: str(pass_or_fail)}
    row.update({src: "1.0" for src in mq.SENSOR_COLUMNS})
    row["SpindleSpeed_max"] = str(speed)
    return row


def _write_csv(path, rows, header=None):
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=header or mq.EXPECTED_HEADER, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


@pytest.fixture()
def loader_rows():
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM machining_quality_record WHERE serial_no LIKE :p"), {"p": LOAD_PREFIX + "%"})


# ---------- 적재 스크립트 ----------

def test_parse_row_maps_columns_and_assumes_kst():
    out = mq.parse_row(_csv_row("S-1", "2022-08-23 03:14:36.906", 1, speed=2226.0))
    assert out["source_received_at"] == datetime(2022, 8, 23, 3, 14, 36, 906000, tzinfo=mq.KST)
    assert out["pass_or_fail"] == 1 and out["spindle_speed_max_raw"] == 2226.0
    assert all(c.endswith("_raw") for c in mq.SENSOR_COLUMNS.values()) and len(mq.SENSOR_COLUMNS) == 40


def test_parse_row_rejects_unknown_label():
    with pytest.raises(ValueError, match="passorfail"):
        mq.parse_row(_csv_row("S-1", "2022-08-23 03:14:36", 2))


def test_read_rows_rejects_unexpected_header(tmp_path):
    path = tmp_path / "bad.csv"
    _write_csv(path, [_csv_row("S-1", "2022-08-23 03:14:36", 0)], header=mq.EXPECTED_HEADER[:-1] + ["label"])
    with pytest.raises(SystemExit, match="label"):
        loader.read_rows(str(path), "utf-8")


def test_replay_shifts_received_at_keeping_intervals():
    rows = [mq.parse_row(_csv_row(f"S-{i}", f"2022-08-23 03:{10 + i}:00", 0)) for i in range(3)]
    start = datetime(2026, 10, 13, 9, 0, tzinfo=mq.KST)
    loader.assign_received_at(rows, start)
    assert [r["received_at"] for r in rows] == [start + timedelta(minutes=i) for i in range(3)]
    assert rows[0]["source_received_at"] == datetime(2022, 8, 23, 3, 10, tzinfo=mq.KST)   # 원본 시각은 그대로
    assert loader.parse_replay_start("2026-10-13T09:00:00") == start                      # 시간대 없으면 KST


def test_load_is_idempotent_and_marks_serial_conflicts(tmp_path, loader_rows):
    path = tmp_path / "sample.csv"
    _write_csv(path, [
        _csv_row(LOAD_PREFIX + "1", "2001-03-01 10:00:00", 0),
        _csv_row(LOAD_PREFIX + "2", "2001-03-01 10:01:00", 0),   # 원본처럼 같은 SerialNo·시각에 0/1 한 쌍
        _csv_row(LOAD_PREFIX + "2", "2001-03-01 10:01:00", 1),
    ])

    def load(replay_start=None):
        rows = loader.read_rows(str(path), "utf-8")
        loader.assign_received_at(rows, replay_start)
        return loader.load(engine, rows)

    first = load()
    assert (first["inserted"], first["after"] - first["before"]) == (3, 3)
    second = load(replay_start=datetime(2001, 3, 5, tzinfo=mq.KST))
    assert (second["inserted"], second["updated"], second["after"]) == (0, 3, first["after"])

    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT serial_no, is_serial_conflict, received_at FROM machining_quality_record "
                 "WHERE serial_no LIKE :p ORDER BY serial_no, pass_or_fail"),
            {"p": LOAD_PREFIX + "%"},
        ).all()
    assert [(r.serial_no[-1], r.is_serial_conflict) for r in rows] == [("1", False), ("2", True), ("2", True)]
    assert rows[0].received_at == datetime(2001, 3, 5, tzinfo=mq.KST)   # 재적재 시 received_at만 이동


# ---------- 조회 API ----------

@pytest.fixture()
def api_rows():
    """2001-04-01 KST: 합격 3건, 불합격 1건(SerialNo 중복 쌍), 아직 도착하지 않은(미래) 불합격 1건"""
    db = SessionLocal()
    try:
        specs = [("A-1", 0, False, 100.0), ("A-2", 0, False, 101.0), ("A-3", 0, True, 99.0), ("A-3", 1, True, 90.0)]
        for i, (serial, pass_or_fail, conflict, speed) in enumerate(specs):
            at = API_START + timedelta(hours=1, minutes=i)
            row = models.MachiningQualityRecord(serial_no=serial, source_received_at=at, received_at=at,
                                                pass_or_fail=pass_or_fail, is_serial_conflict=conflict,
                                                **{c: 1.0 for c in mq.SENSOR_COLUMNS.values()})
            row.spindle_speed_max_raw = speed
            db.add(row)
        db.add(models.MachiningQualityRecord(serial_no="A-future", source_received_at=API_START + timedelta(hours=2),
                                             received_at=datetime.now(timezone.utc) + timedelta(days=1), pass_or_fail=1,
                                             **{c: 1.0 for c in mq.SENSOR_COLUMNS.values()}))
        db.commit()
    finally:
        db.close()


def test_list_filters_and_hides_future_rows(client, api_rows):
    body = client.get("/machining-quality", params=API_RANGE).json()
    assert body["total"] == 4 and [r["serial_no"] for r in body["records"]] == ["A-1", "A-2", "A-3", "A-3"]
    assert set(body["records"][0]["sensors"]) == set(mq.FEATURE_COLUMNS)   # 상수 열은 기본 제외

    fails = client.get("/machining-quality", params={**API_RANGE, "pass_or_fail": 1}).json()
    assert [r["serial_no"] for r in fails["records"]] == ["A-3"]
    no_conflict = client.get("/machining-quality", params={**API_RANGE, "include_serial_conflict": "false"}).json()
    assert no_conflict["total"] == 2

    limited = client.get("/machining-quality", params={**API_RANGE, "limit": 1, "include_constant": "true"}).json()
    assert (limited["returned"], limited["truncated"]) == (1, True)
    assert set(limited["records"][0]["sensors"]) == set(mq.SENSOR_COLUMNS.values())


def test_list_csv_export(client, api_rows):
    res = client.get("/machining-quality", params={**API_RANGE, "format": "csv"})
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/csv")
    rows = list(csv.DictReader(io.StringIO(res.text)))
    assert len(rows) == 4 and list(rows[0])[:5] == ["record_id", "serial_no", "received_at", "source_received_at", "pass_or_fail"]


def test_summary_fail_rate_and_sensor_comparison(client, api_rows):
    body = client.get("/machining-quality/summary", params=API_RANGE).json()
    assert (body["total"], body["fail"], body["fail_rate"]) == (4, 1, 0.25)
    assert body["buckets"][0]["bucket_start"].startswith("2001-04-01T00:00:00")
    assert len(body["excluded_constant_columns"]) == len(mq.CONSTANT_COLUMNS)
    # 불합격이 1건이라 표준편차를 계산할 수 없어 effect_size는 비어 있다 (평균은 비교 가능)
    speed = next(c for c in body["sensor_comparison"] if c["column"] == "spindle_speed_max_raw")
    assert (speed["pass_mean"], speed["fail_mean"], speed["effect_size"]) == (100.0, 90.0, None)


@pytest.fixture()
def api_predictions(api_rows):
    """A-1은 학습 기간이라 예측 없음, A-2 합격→합격, A-3 쌍(불합격→불합격, 합격→불합격 오탐), 미래 행 예측은 숨김 대상.
    행 삭제 시 ON DELETE CASCADE로 함께 지워진다"""
    db = SessionLocal()
    try:
        ids = {(r.serial_no, r.pass_or_fail): r.record_id for r in db.query(models.MachiningQualityRecord)
               .filter(models.MachiningQualityRecord.source_received_at >= API_START,
                       models.MachiningQualityRecord.source_received_at < API_START + timedelta(days=1))}
        for key, prob in {("A-2", 0): 0.1, ("A-3", 1): 0.9, ("A-3", 0): 0.6, ("A-future", 1): 0.8}.items():
            db.add(models.MachiningPrediction(record_id=ids[key], fail_probability=prob, predicted_fail=int(prob >= 0.5),
                                              model_name="logistic_regression", threshold=0.5,
                                              trained_until=API_START + timedelta(hours=1, minutes=1),
                                              train_rows=1, train_fail=0))
        db.commit()
    finally:
        db.close()


def test_summary_prediction_compares_only_predicted_arrived_rows(client, api_predictions):
    pred = client.get("/machining-quality/summary", params=API_RANGE).json()["prediction"]
    assert (pred["evaluated"], pred["actual_fail"], pred["predicted_fail"]) == (3, 1, 2)   # A-1(학습)·미래 행 제외
    assert (pred["true_positive"], pred["false_positive"], pred["false_negative"], pred["true_negative"]) == (1, 1, 0, 1)
    assert (pred["precision"], pred["recall"], pred["agreement_rate"]) == (0.5, 1.0, 0.6667)
    assert pred["note"]

    no_conflict = client.get("/machining-quality/summary",
                             params={**API_RANGE, "include_serial_conflict": "false"}).json()["prediction"]
    assert (no_conflict["evaluated"], no_conflict["precision"], no_conflict["recall"]) == (1, None, None)


@pytest.mark.parametrize("path, params, status", [
    ("/machining-quality", {"start": "2001-04-02T00:00:00+09:00", "end": "2001-04-01T00:00:00+09:00"}, 400),
    ("/machining-quality", {"pass_or_fail": 2}, 422),
    ("/machining-quality", {"format": "xml"}, 422),
    ("/machining-quality/summary", {"bucket": "week"}, 422),
])
def test_invalid_requests_are_rejected(client, path, params, status):
    assert client.get(path, params=params).status_code == status
