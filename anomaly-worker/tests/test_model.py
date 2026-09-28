import os
from datetime import datetime, timezone

import numpy as np
import pytest

from features import extract_features
from model import fit_model, load_bundle, model_path, save_bundle, score


def _normal_rows(n: int, seed: int = 0):
    """시뮬레이터 정상 구간과 비슷한 분포: 사이클타임 12초±5%, 60초 구간, 불량률 약 3%"""
    rng = np.random.default_rng(seed)
    rows = []
    for _ in range(n):
        cycle = rng.normal(12.0, 0.6)
        total = round(60 * rng.uniform(0.85, 0.98) / cycle)
        defect = int(rng.binomial(total, 0.03))
        rows.append((cycle, total - defect, defect))
    return rows


@pytest.fixture(scope="module")
def trained():
    X = extract_features(_normal_rows(300))
    return X, fit_model(X, threshold_quantile=0.99)


def test_threshold_is_quantile_of_training_scores(trained):
    X, bundle = trained
    scores, flags = score(bundle, X)
    assert bundle["threshold"] == pytest.approx(np.quantile(scores, 0.99))
    # 정상(학습) 데이터 중 threshold를 넘는 비율은 약 1%
    assert flags.mean() <= 0.02


def test_score_shapes_and_flag_matches_threshold(trained):
    X, bundle = trained
    scores, flags = score(bundle, X[:10])
    assert scores.shape == (10,) and flags.shape == (10,)
    assert (flags == (scores > bundle["threshold"])).all()


def test_obvious_anomaly_scores_higher_than_normal(trained):
    X, bundle = trained
    # 사이클타임 3배 + 생산량 급감 + 불량률 40%
    anomaly = extract_features([(36.0, 1, 1)])
    normal_scores, _ = score(bundle, X)
    anomaly_score, anomaly_flag = score(bundle, anomaly)
    assert anomaly_score[0] > np.median(normal_scores)
    assert anomaly_flag[0]


def test_save_and_load_roundtrip(tmp_path, trained):
    X, bundle = trained
    bundle = dict(bundle, train_rows=len(X),
                  train_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
                  train_end=datetime(2026, 1, 2, tzinfo=timezone.utc))
    path = save_bundle(bundle, str(tmp_path), "EQ-TEST")
    assert path == model_path(str(tmp_path), "EQ-TEST")
    assert not os.path.exists(path + ".tmp")

    loaded = load_bundle(str(tmp_path), "EQ-TEST")
    np.testing.assert_allclose(score(loaded, X)[0], score(bundle, X)[0])
    assert loaded["threshold"] == bundle["threshold"]


def test_load_missing_model_returns_none(tmp_path):
    assert load_bundle(str(tmp_path), "EQ-NONE") is None
