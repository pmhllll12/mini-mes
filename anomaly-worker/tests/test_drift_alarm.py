from datetime import datetime, timedelta, timezone

import numpy as np
from prometheus_client import REGISTRY

import worker
from features import build_features
from model import fit_model, save_bundle
from worker import DriftAlarm, ModelCache, run_drift


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
