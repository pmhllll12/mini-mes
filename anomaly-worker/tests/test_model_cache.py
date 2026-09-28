import os
from datetime import datetime, timezone

from features import extract_features
from model import fit_model, save_bundle
from worker import ModelCache


def _bundle(cycle: float) -> dict:
    X = extract_features([(cycle + i * 0.01, 5, 0) for i in range(60)])
    bundle = fit_model(X)
    bundle.update(train_rows=len(X),
                  train_start=datetime(2026, 1, 1, tzinfo=timezone.utc),
                  train_end=datetime(2026, 1, 2, tzinfo=timezone.utc))
    return bundle


def test_missing_model_returns_none(tmp_path):
    # 모델이 없으면 예외 없이 None -> 워커는 "모델 없음" 로그 후 대기
    assert ModelCache(str(tmp_path)).get("EQ-001") is None


def test_reloads_when_model_file_changes(tmp_path):
    cache = ModelCache(str(tmp_path))
    path = save_bundle(_bundle(12.0), str(tmp_path), "EQ-001")
    first = cache.get("EQ-001")
    assert cache.get("EQ-001") is first  # 파일이 그대로면 캐시 사용

    save_bundle(_bundle(15.0), str(tmp_path), "EQ-001")
    st = os.stat(path)
    os.utime(path, (st.st_atime, st.st_mtime + 10))  # mtime 해상도와 무관하게 변경을 보장
    second = cache.get("EQ-001")
    assert second is not first
