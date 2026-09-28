import numpy as np

from features import FEATURE_NAMES, build_features, extract_features, feature_names


def test_extract_features_values():
    X = extract_features([(12.0, 9, 1), (15.5, 4, 0)])
    assert X.shape == (2, len(FEATURE_NAMES))
    np.testing.assert_allclose(X[0], [12.0, 10.0, 0.1])
    np.testing.assert_allclose(X[1], [15.5, 4.0, 0.0])


def test_extract_features_zero_production_has_zero_defect_rate():
    # 생산 0건 구간에서 0으로 나누지 않아야 한다
    X = extract_features([(12.0, 0, 0)])
    np.testing.assert_allclose(X[0], [12.0, 0.0, 0.0])


def test_extract_features_accepts_db_types_and_none_qty():
    from decimal import Decimal

    # psycopg2는 NUMERIC을 Decimal로 돌려준다
    X = extract_features([(Decimal("8.25"), None, 2)])
    np.testing.assert_allclose(X[0], [8.25, 2.0, 1.0])


def test_extract_features_empty_input_keeps_shape():
    X = extract_features([])
    assert X.shape == (0, len(FEATURE_NAMES))


def test_build_features_without_window_matches_extract():
    rows = [(12.0, 9, 1), (15.5, 4, 0)]
    np.testing.assert_allclose(build_features(rows, 0), extract_features(rows))


def test_build_features_rolling_median_and_pooled_defect_rate():
    rows = [(10.0, 4, 0), (30.0, 3, 1), (11.0, 4, 0), (12.0, 3, 1)]
    X = build_features(rows, window=3)
    assert X.shape == (4, len(feature_names(3)))
    # 앞쪽은 있는 구간만으로 계산: 1구간 -> 자기 자신
    np.testing.assert_allclose(X[0, 3:], [10.0, 0.0])
    # 3구간 (10, 30, 11): 튀는 30은 중앙값에 반영되지 않음, 불량 1 / 생산 12
    np.testing.assert_allclose(X[2, 3:], [11.0, 1 / 12])
    # 최근 3구간 (30, 11, 12): 불량 2 / 생산 12
    np.testing.assert_allclose(X[3, 3:], [12.0, 2 / 12])
