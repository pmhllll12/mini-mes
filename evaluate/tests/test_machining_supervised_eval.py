"""정밀가공 지도 학습 평가의 쌍 단위 분할·지표 (DB 없이)"""
import numpy as np
import pytest

import machining_supervised_eval as mse


def test_group_ids_keep_pairs_together():
    assert list(mse.group_ids(["a", "b", "a", "c", "b"])) == [0, 1, 0, 2, 1]


def test_time_split_sends_boundary_pair_to_test():
    times = np.array(["t1", "t2", "t3", "t3", "t4"])
    groups = mse.group_ids(["x", "y", "p", "p", "z"])
    train, test = mse.time_split(times, groups, train_frac=0.6)   # 경계(3행째)가 쌍 p의 한가운데
    assert list(train) == [0, 1] and list(test) == [2, 3, 4]


def test_cv_predict_never_splits_a_pair():
    rng = np.random.default_rng(0)
    n_pairs, n_clean = 20, 80
    X = rng.normal(size=(2 * n_pairs + n_clean, 3))
    y = np.array([0, 1] * n_pairs + [0] * n_clean)
    X[y == 1, 0] += 4
    keys = [f"p{i}" for i in range(n_pairs) for _ in range(2)] + [f"c{i}" for i in range(n_clean)]
    groups = mse.group_ids(keys)
    prob = mse.cv_predict(X, y, groups, "logistic", folds=4, seed=0)
    assert not np.isnan(prob).any()
    m = mse.evaluate_predictions(prob, y, np.array([True] * 2 * n_pairs + [False] * n_clean), keys)
    assert m["auc"] > 0.95 and m["pair"]["pairs"] == n_pairs and m["pair_pass_fpr"] is not None


def test_evaluate_predictions_threshold_and_pair_metrics():
    prob = np.array([0.1, 0.9, 0.6, 0.4, 0.2, 0.7])
    y = np.array([0, 1, 0, 1, 0, 0])
    conflict = np.array([True, True, True, True, False, False])
    keys = ["a", "a", "b", "b", "c", "d"]
    m = mse.evaluate_predictions(prob, y, conflict, keys)
    assert (m["precision"], m["recall"]) == (pytest.approx(1 / 3), 0.5)
    assert m["clean_pass_fpr"] == 0.5 and m["pair_pass_fpr"] == 0.5
    assert m["pair"] == {"pairs": 2, "fail_higher": 1, "ties": 0, "rate": 0.5}
    assert m["pair_auc"] == 0.75
    test_only = mse.evaluate_predictions(prob, y, conflict, keys, idx=np.array([0, 1]))
    assert test_only["n"] == 2 and test_only["auc"] == 1.0
