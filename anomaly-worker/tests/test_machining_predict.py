from datetime import datetime
from pathlib import Path
import importlib.util

import numpy as np
import pytest

import machining_predict as mp

API_MQ = Path(__file__).resolve().parents[2] / "api" / "machining_quality.py"


@pytest.mark.skipif(not API_MQ.exists(), reason="저장소 밖(이미지 안)에서 실행 - api 코드 없음")
def test_feature_columns_match_api():
    spec = importlib.util.spec_from_file_location("api_machining_quality", API_MQ)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert mp.FEATURE_COLUMNS == module.FEATURE_COLUMNS


def test_parse_cutoff_assumes_kst():
    assert mp.parse_cutoff("2022-08-25") == datetime(2022, 8, 25, tzinfo=mp.KST)
    assert mp.parse_cutoff("2022-08-25T00:00:00+00:00").utcoffset().total_seconds() == 0


def test_split_by_cutoff_keeps_same_time_rows_together():
    cutoff = datetime(2022, 8, 25, tzinfo=mp.KST)
    times = [datetime(2022, 8, 24, 23, 59, tzinfo=mp.KST), cutoff, cutoff, datetime(2022, 8, 25, 1, tzinfo=mp.KST)]
    train, pred = mp.split_by_cutoff(times, cutoff)
    assert train.tolist() == [0] and pred.tolist() == [1, 2, 3]   # 같은 시각(중복 쌍)은 함께 예측 쪽


def test_fit_predict_separates_obvious_classes_and_needs_both_labels():
    rng = np.random.default_rng(0)
    X_train = np.vstack([rng.normal(0, 1, (40, 3)), rng.normal(5, 1, (10, 3))])
    y_train = np.array([0] * 40 + [1] * 10)
    prob = mp.fit_predict(X_train, y_train, np.array([[0.0, 0.0, 0.0], [5.0, 5.0, 5.0]]))
    assert prob[0] < mp.THRESHOLD < prob[1]
    with pytest.raises(SystemExit, match="합격·불합격"):
        mp.fit_predict(X_train[:40], y_train[:40], X_train[:1])


def test_confusion_counts():
    assert mp.confusion(np.array([1, 1, 0, 0, 0]), np.array([1, 0, 1, 0, 0])) == {"tp": 1, "fp": 1, "fn": 1, "tn": 2}
