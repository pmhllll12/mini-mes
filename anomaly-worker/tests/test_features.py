import numpy as np

from features import FEATURE_NAMES, extract_features


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
