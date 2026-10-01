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


# ---------- CUSUM 시작 · 사이클타임 z 해제 (선택 옵션) ----------

def test_options_off_keep_model_only_behavior():
    """옵션을 주지 않으면 cycle_z를 넘겨도 모델 판정만 따른다"""
    alarm = DriftAlarm(2, 2)
    assert not alarm.uses_cycle_z
    assert alarm.update("EQ-001", [False] * 5, cycle_z=[9.0] * 5) == []


def test_cusum_raises_on_small_persistent_shift():
    """모델이 정상으로 보는 작은 상승(z=2)이 계속되면 CUSUM이 쌓여 경보 시작: (2-1)*5 = 5 > 4"""
    alarm = DriftAlarm(2, 2, cusum_k=1.0, cusum_h=4.0)
    assert alarm.update("EQ-001", [False] * 6, cycle_z=[2.0] * 6) == [(4, "raised")]


def test_cusum_ignores_normal_noise_and_held_intervals():
    alarm = DriftAlarm(2, 2, cusum_k=1.0, cusum_h=4.0)
    # k 이하의 변동은 쌓이지 않음
    assert alarm.update("EQ-001", [False] * 50, cycle_z=[0.9, -0.5] * 25) == []
    # 판단 보류(None, 급변)는 z가 커도 더하지 않음
    assert alarm.update("EQ-002", [None] * 5, cycle_z=[30.0] * 5) == []
    # 튀는 1건은 CUSUM_Z_CAP(4)까지만 더함 - 한 건만으로는 경보가 되지 않음
    assert alarm.update("EQ-003", [False], cycle_z=[30.0]) == []


def test_clear_uses_cycle_z_instead_of_rolling_model():
    """이동 구간 모델은 열화가 끝난 뒤에도 열화로 판정하지만, 현재 구간 z가 돌아오면 2구간 만에 해제"""
    alarm = DriftAlarm(2, 2, clear_z=1.5)
    assert alarm.update("EQ-001", [True, True], cycle_z=[3.0, 3.0]) == [(1, "raised")]
    assert alarm.update("EQ-001", [True, True, True], cycle_z=[0.2, 2.0, 0.1]) == []  # 연속이 끊김
    assert alarm.update("EQ-001", [True], cycle_z=[0.3]) == [(0, "cleared")]


def test_cusum_resets_after_clear():
    alarm = DriftAlarm(2, 2, cusum_k=1.0, cusum_h=4.0, clear_z=1.5)
    events = alarm.update("EQ-001", [False] * 5 + [False, False], cycle_z=[2.0] * 5 + [0.0, 0.0])
    assert events == [(4, "raised"), (6, "cleared")]
    # 해제 후 다시 0부터 쌓는다 - z=2 한 번으로는 다시 켜지지 않음
    assert alarm.update("EQ-001", [False], cycle_z=[2.0]) == []


def test_cycle_z_uses_training_center_and_scale():
    bundle = {"z_center": np.array([10.0, 5.0]), "z_scale": np.array([0.5, 1.0])}
    assert worker.cycle_z(bundle, np.array([[11.0, 0.0], [9.5, 0.0]])).tolist() == [2.0, -1.0]
