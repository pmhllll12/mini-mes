"""정밀가공 이상탐지 평가의 분할·지표·쌍 비교 (DB 없이, 워커 모델 코드는 저장소의 anomaly-worker 사용)"""
import os

import numpy as np
import pytest

import machining_anomaly_eval as mae


@pytest.fixture(autouse=True)
def worker_dir(monkeypatch):
    monkeypatch.setattr(mae, "WORKER_APP_DIR", os.path.join(os.path.dirname(__file__), "..", "..", "anomaly-worker"))


def test_split_trains_only_on_clean_passes():
    label = np.array([0, 0, 0, 0, 0, 0, 0, 0, 0, 1, 0, 1])
    conflict = np.array([False] * 8 + [True] * 4)
    g = mae.split_groups(label, conflict, holdout=0.25, seed=0)
    assert len(g["holdout"]) == 2 and len(g["train"]) == 6
    assert set(g["train"]) | set(g["holdout"]) == set(range(8))
    assert list(g["pair_pass"]) == [8, 10] and list(g["fail"]) == [9, 11]
    assert not set(g["train"]) & {8, 9, 10, 11}   # 쌍의 행·불합격은 학습에 들어가지 않음


def test_compute_metrics_and_auc():
    m = mae.compute_metrics(np.array([3.0, 2.0, 0.5]), np.array([0.1, 0.2, 1.5, 0.3]), threshold=1.0)
    assert (m["tp"], m["fn"], m["fp"], m["tn"]) == (2, 1, 1, 3)
    assert m["precision"] == pytest.approx(2 / 3) and m["recall"] == pytest.approx(2 / 3)
    assert m["auc"] == pytest.approx(11 / 12)          # 12쌍 중 0.5 < 1.5 한 쌍만 역전
    assert mae.auc(np.array([1.0]), np.array([1.0])) == 0.5
    assert mae.auc(np.array([]), np.array([1.0])) is None


def test_pair_win_rate_matches_rows_by_key():
    scores = np.array([0.1, 0.9, 0.8, 0.2, 0.5, 0.5, 0.3])
    label = np.array([0, 1, 0, 1, 0, 1, 0])
    keys = ["a", "a", "b", "b", "c", "c", "solo"]
    assert mae.pair_win_rate(scores, label, keys) == {"pairs": 3, "fail_higher": 1, "ties": 1, "rate": 0.5}


def test_evaluate_seed_separates_shifted_failures():
    rng = np.random.default_rng(1)
    normal = rng.normal(0, 1, size=(200, 4))
    pair_pass = rng.normal(0, 1, size=(20, 4))
    fail = rng.normal(0, 1, size=(20, 4)) + np.array([6, 0, 0, 0])
    X = np.vstack([normal, pair_pass, fail])
    data = {
        "X": X,
        "label": np.array([0] * 220 + [1] * 20),
        "conflict": np.array([False] * 200 + [True] * 40),
        "pair_key": [f"n{i}" for i in range(200)] + [f"p{i}" for i in range(20)] * 2,
    }
    r = mae.evaluate_seed(data, holdout=0.3, quantile=0.99, seed=0)
    assert r["sizes"] == {"train": 140, "holdout": 60, "pair_pass": 20, "fail": 20}
    assert r["metrics"]["with_pair_pass"]["recall"] == 1.0
    assert r["metrics"]["without_pair_pass"]["auc"] > 0.99
    assert r["pair"]["rate"] == 1.0
    s = mae.summarize([r, r])
    assert s["pair_win_rate"] == {"mean": 1.0, "min": 1.0, "max": 1.0}
