import os
from datetime import datetime, timezone

import numpy as np
import pytest

from features import extract_features
from model import fit_model, load_bundle, model_path, robust_center_scale, save_bundle, score


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


def test_score_keeps_growing_outside_training_range(trained):
    """Isolation Forest 단독(v1)은 학습 범위 밖에서 점수가 포화됐다. v2는 멀어질수록 커져야 한다."""
    X, bundle = trained
    med = np.median(X, axis=0)
    rows = []
    for factor in (1.3, 2.0, 3.0):
        x = med.copy()
        x[0] *= factor   # 사이클타임 증가
        x[1] /= factor   # 그만큼 생산 수량 감소, 불량률은 정상 중앙값 그대로
        rows.append(x)
    scores, flags = score(bundle, np.asarray(rows))
    assert scores[0] < scores[1] < scores[2]
    # 불량률은 정상인데 사이클타임만 2배 이상 - v1이 놓치던 유형
    assert flags[1] and flags[2]


def test_robust_scale_falls_back_when_mad_is_zero():
    # 불량률이 대부분 0인 설비: MAD=0 -> 표준편차 사용, 모두 같으면 1
    X = np.array([[12.0, 5.0, 0.0]] * 9 + [[12.0, 5.0, 0.25]])
    center, scale = robust_center_scale(X)
    np.testing.assert_allclose(center, [12.0, 5.0, 0.0])
    assert scale[2] == pytest.approx(X[:, 2].std())
    assert scale[0] == 1.0 and scale[1] == 1.0


def test_legacy_v1_bundle_still_scores(trained):
    X, bundle = trained
    legacy = {"model": bundle["model"], "threshold": 0.7, "threshold_quantile": 0.99}
    scores, flags = score(legacy, X[:5])
    np.testing.assert_allclose(scores, -bundle["model"].score_samples(X[:5]))
    assert (flags == (scores > 0.7)).all()
