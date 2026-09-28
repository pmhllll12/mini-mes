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


def test_features_for_prepends_context_for_rolling_model(monkeypatch):
    import worker

    context = [(datetime(2026, 1, 1, 0, i, tzinfo=timezone.utc), 12.0, 5, 0) for i in range(4)]
    rows = [(datetime(2026, 1, 1, 1, 0, tzinfo=timezone.utc), 36.0, 1, 1)]
    calls = []

    def fake_context(conn, equipment_id, before, limit):
        calls.append((equipment_id, before, limit))
        return context[-limit:]

    monkeypatch.setattr(worker, "fetch_context_rows", fake_context)

    X = worker.features_for(None, "EQ-001", rows, {"rolling_window": 5})
    assert calls == [("EQ-001", rows[0][0], 4)]  # 직전 window-1건 이력 요청
    assert X.shape == (1, 5)
    # 튀는 1건(36초)은 이력 4건(12초)과 합쳐진 중앙값에 반영되지 않음
    assert X[0, 3] == 12.0

    X0 = worker.features_for(None, "EQ-001", rows, {})  # 이동 구간 특징 없는 모델은 이력을 조회하지 않음
    assert X0.shape == (1, 3) and len(calls) == 1
