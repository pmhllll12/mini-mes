from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from prometheus_client import REGISTRY

import worker
from features import build_features
from model import fit_model, save_bundle
from worker import DriftAlarm, ModelCache, run_drift


@pytest.fixture(autouse=True)
def alarm_writes(monkeypatch):
    """drift_alarm 기록을 DB 대신 목록에 남긴다 (워커 테스트는 DB 없이 실행)"""
    writes = []
    monkeypatch.setattr(worker, "open_drift_alarm",
                        lambda conn, eq, ts, score, threshold: writes.append(("open", eq, ts, score, threshold)))
    monkeypatch.setattr(worker, "close_drift_alarm", lambda conn, eq, ts: writes.append(("close", eq, ts)))
    return writes


def test_alarm_transitions_across_batches():
    alarm = DriftAlarm()
    assert alarm.update("EQ-001", [False, True, True]) == [(1, "raised")]
    # 다음 주기로 경보 상태가 이어진다 (중복 발생 없음)
    assert alarm.update("EQ-001", [True, False]) == [(1, "cleared")]
    assert alarm.update("EQ-001", [False]) == []
    assert alarm.active["EQ-001"] is False
    # 설비별로 독립
    assert alarm.update("EQ-002", [True]) == [(0, "raised")]


def _rows(cycles, start):
    return [(start + timedelta(minutes=i), c, 5, 0) for i, c in enumerate(cycles)]


def test_run_drift_raises_alarm_and_exports_metrics(tmp_path, monkeypatch):
    rng = np.random.default_rng(1)
    train = _rows(rng.normal(12.0, 0.6, 200), datetime(2026, 1, 1, tzinfo=timezone.utc))
    bundle = fit_model(build_features((r[1:] for r in train), 5), rolling_window=5)
    bundle.update(train_rows=len(train), train_start=train[0][0], train_end=train[-1][0])
    save_bundle(bundle, str(tmp_path), "EQ-DRIFT")

    monkeypatch.setattr(worker, "fetch_context_rows", lambda conn, eq, before, limit: train[-limit:])
    # 정상 5구간 뒤 사이클타임이 1.3배로 올라간 상태가 이어짐
    rows = _rows([12.0] * 5 + [15.6] * 10, datetime(2026, 1, 2, tzinfo=timezone.utc))
    alarm = DriftAlarm()
    before = REGISTRY.get_sample_value("mes_drift_alarm_raised_total", {"equipment_id": "EQ-DRIFT"}) or 0

    run_drift(None, "EQ-DRIFT", rows, ModelCache(str(tmp_path), label="열화 "), alarm)

    assert alarm.active["EQ-DRIFT"] is True
    assert REGISTRY.get_sample_value("mes_drift_alarm", {"equipment_id": "EQ-DRIFT"}) == 1
    assert REGISTRY.get_sample_value("mes_drift_alarm_raised_total", {"equipment_id": "EQ-DRIFT"}) == before + 1
    assert REGISTRY.get_sample_value("mes_drift_model_loaded", {"equipment_id": "EQ-DRIFT"}) == 1


def test_run_drift_skips_when_model_missing(tmp_path):
    alarm = DriftAlarm()
    run_drift(None, "EQ-NOMODEL", _rows([12.0], datetime(2026, 1, 1, tzinfo=timezone.utc)),
              ModelCache(str(tmp_path)), alarm)
    assert "EQ-NOMODEL" not in alarm.active
    assert REGISTRY.get_sample_value("mes_drift_model_loaded", {"equipment_id": "EQ-NOMODEL"}) == 0


def test_rows_flagged_as_spike_do_not_raise_drift_alarm(tmp_path, monkeypatch):
    rng = np.random.default_rng(2)
    train = _rows(rng.normal(12.0, 0.6, 200), datetime(2026, 1, 1, tzinfo=timezone.utc))
    bundle = fit_model(build_features((r[1:] for r in train), 5), rolling_window=5)
    bundle.update(train_rows=len(train), train_start=train[0][0], train_end=train[-1][0])
    save_bundle(bundle, str(tmp_path), "EQ-SPIKE")
    monkeypatch.setattr(worker, "fetch_context_rows", lambda conn, eq, before, limit: train[-limit:])

    # 정상 사이에 급변 1건 (사이클타임 3배) - 급변 탐지기가 이미 잡은 건
    rows = _rows([12.0, 12.0, 36.0, 12.0, 12.0], datetime(2026, 1, 2, tzinfo=timezone.utc))
    alarm = DriftAlarm()
    run_drift(None, "EQ-SPIKE", rows, ModelCache(str(tmp_path)), alarm,
              spike_flags=[False, False, True, False, False])
    assert alarm.active["EQ-SPIKE"] is False
    assert (REGISTRY.get_sample_value("mes_drift_alarm_raised_total", {"equipment_id": "EQ-SPIKE"}) or 0) == 0


def test_hysteresis_ignores_single_blips_and_holds_through_short_dips():
    alarm = DriftAlarm(raise_after=2, clear_after=3)
    # 1구간짜리 열화 판정은 무시
    assert alarm.update("EQ-H", [True, False, True, False]) == []
    # 2구간 연속이면 두 번째 구간에서 시작
    assert alarm.update("EQ-H", [True, True]) == [(1, "raised")]
    # 경보 중 정상 2구간은 버팀, 다시 열화가 오면 연속 횟수 초기화
    assert alarm.update("EQ-H", [False, False, True, False, False]) == []
    assert alarm.active["EQ-H"] is True
    # 연속 횟수는 판정 주기를 넘어 이어짐: 앞 주기 정상 2구간 + 이번 1구간 = 3구간 -> 해제
    assert alarm.update("EQ-H", [False]) == [(0, "cleared")]
    assert alarm.active["EQ-H"] is False


def test_spike_rows_are_neutral_and_do_not_clear_active_alarm():
    alarm = DriftAlarm(raise_after=2, clear_after=3)
    assert alarm.update("EQ-N", [True, True]) == [(1, "raised")]
    # 열화 후반을 급변 탐지기가 잡아 판단 보류(None)가 이어져도 경보 유지
    assert alarm.update("EQ-N", [None, None, None, None]) == []
    assert alarm.active["EQ-N"] is True
    # None은 정상 연속 횟수를 끊지도 늘리지도 않음: 정상 2 + 보류 + 정상 1 = 3 -> 해제
    assert alarm.update("EQ-N", [False, False, None, False]) == [(3, "cleared")]


def _drift_bundle(tmp_path, monkeypatch, equipment_id, seed):
    rng = np.random.default_rng(seed)
    train = _rows(rng.normal(12.0, 0.6, 200), datetime(2026, 1, 1, tzinfo=timezone.utc))
    bundle = fit_model(build_features((r[1:] for r in train), 5), rolling_window=5)
    bundle.update(train_rows=len(train), train_start=train[0][0], train_end=train[-1][0])
    save_bundle(bundle, str(tmp_path), equipment_id)
    monkeypatch.setattr(worker, "fetch_context_rows", lambda conn, eq, before, limit: train[-limit:])
    return bundle


def test_alarm_start_and_clear_are_recorded(tmp_path, monkeypatch, alarm_writes):
    bundle = _drift_bundle(tmp_path, monkeypatch, "EQ-REC", 3)
    models = ModelCache(str(tmp_path))
    alarm = DriftAlarm()

    rows = _rows([12.0] * 5 + [15.6] * 10, datetime(2026, 1, 2, tzinfo=timezone.utc))
    run_drift(None, "EQ-REC", rows, models, alarm)
    assert len(alarm_writes) == 1
    kind, eq, ts, score, threshold = alarm_writes[0]
    assert (kind, eq) == ("open", "EQ-REC")
    assert ts in [r[0] for r in rows[5:]]  # 열화가 시작된 뒤의 생산실적 ts
    assert score > threshold == bundle["threshold"]

    # 다음 주기에 정상으로 돌아오면 해제 기록 (이동 구간이 정상으로 채워진 뒤)
    back = _rows([12.0] * 15, datetime(2026, 1, 3, tzinfo=timezone.utc))
    monkeypatch.setattr(worker, "fetch_context_rows", lambda conn, eq, before, limit: rows[-limit:])
    run_drift(None, "EQ-REC", back, models, alarm)
    assert [w[0] for w in alarm_writes] == ["open", "close"]
    assert alarm_writes[1][2] in [r[0] for r in back]
    assert alarm.active["EQ-REC"] is False


def test_restored_alarm_is_not_raised_again(tmp_path, monkeypatch, alarm_writes):
    _drift_bundle(tmp_path, monkeypatch, "EQ-RST", 4)
    alarm = DriftAlarm()
    alarm.restore(["EQ-RST"])  # 재시작 전부터 경보 중 (drift_alarm의 진행 중 행)

    rows = _rows([15.6] * 10, datetime(2026, 1, 2, tzinfo=timezone.utc))
    run_drift(None, "EQ-RST", rows, ModelCache(str(tmp_path)), alarm)
    assert alarm.active["EQ-RST"] is True
    assert alarm_writes == []  # 이미 경보 중이므로 새 경보 행을 만들지 않음
