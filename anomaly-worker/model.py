"""
설비별 Isolation Forest 모델 학습·점수 계산·저장.

anomaly_score = -IsolationForest.score_samples(X)
  -> 값이 클수록 더 이상함 (대략 0.3~0.8 범위)
threshold = 학습(정상) 데이터 anomaly_score의 threshold_quantile 분위수
  -> 정상 데이터 기준 오탐률이 약 (1 - threshold_quantile)이 되도록 잡는다.
"""
import os
from datetime import datetime
from typing import Optional

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

from features import FEATURE_NAMES

DEFAULT_THRESHOLD_QUANTILE = 0.99


def fit_model(
    X: np.ndarray,
    threshold_quantile: float = DEFAULT_THRESHOLD_QUANTILE,
    random_state: int = 42,
) -> dict:
    model = IsolationForest(n_estimators=200, random_state=random_state)
    model.fit(X)
    train_scores = -model.score_samples(X)
    return {
        "model": model,
        "threshold": float(np.quantile(train_scores, threshold_quantile)),
        "threshold_quantile": threshold_quantile,
        "feature_names": list(FEATURE_NAMES),
    }


def score(bundle: dict, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(anomaly_score, is_anomaly) 반환"""
    scores = -bundle["model"].score_samples(X)
    return scores, scores > bundle["threshold"]


def model_path(model_dir: str, equipment_id: str) -> str:
    return os.path.join(model_dir, f"{equipment_id}.joblib")


def save_bundle(bundle: dict, model_dir: str, equipment_id: str) -> str:
    os.makedirs(model_dir, exist_ok=True)
    path = model_path(model_dir, equipment_id)
    tmp = path + ".tmp"
    joblib.dump(bundle, tmp)
    os.replace(tmp, path)  # 워커가 반쯤 쓰인 파일을 읽지 않도록 원자적 교체
    return path


def load_bundle(model_dir: str, equipment_id: str) -> Optional[dict]:
    path = model_path(model_dir, equipment_id)
    if not os.path.exists(path):
        return None
    return joblib.load(path)


def describe(bundle: dict) -> str:
    def fmt(ts: datetime) -> str:
        return ts.isoformat(timespec="seconds")

    return (
        f"rows={bundle['train_rows']} "
        f"range={fmt(bundle['train_start'])}~{fmt(bundle['train_end'])} "
        f"threshold={bundle['threshold']:.4f} (q={bundle['threshold_quantile']})"
    )
