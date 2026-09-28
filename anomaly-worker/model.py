"""
설비별 이상 점수 모델 학습·점수 계산·저장 (Isolation Forest + 범위 이탈 robust z-score).

Isolation Forest는 학습 범위를 벗어난 값을 "얼마나 멀리 벗어났는지"와 관계없이 같은 점수로 매긴다(점수 포화).
그래서 학습 범위 밖으로 멀어질수록 커지는 robust z-score를 함께 쓴다 (version 2).

- if_score = -IsolationForest.score_samples(X)                       (클수록 이상)
- z_score  = max_i |x_i - median_i| / scale_i                        (특징별 robust z의 최댓값)
    scale_i = 1.4826 * MAD_i, MAD가 0이면 표준편차, 그것도 0이면 1  (모두 학습 데이터 기준)
- anomaly_score = max(if_score / if_threshold, z_score / z_threshold)
    if_threshold, z_threshold = 각 점수의 학습 데이터 threshold_quantile 분위수
- threshold = 학습 데이터 anomaly_score의 threshold_quantile 분위수 (1 근처)
  -> 모든 기준값이 학습(정상) 데이터에서만 정해지며, 정상 데이터 기준 오탐률은 약 (1 - threshold_quantile).

version 1(Isolation Forest 단독, anomaly_score = if_score) 번들도 그대로 점수를 계산할 수 있다.
"""
import os
from datetime import datetime
from typing import Optional

import joblib
import numpy as np
from sklearn.ensemble import IsolationForest

from features import FEATURE_NAMES

DEFAULT_THRESHOLD_QUANTILE = 0.99
MODEL_VERSION = 2

MAD_TO_STD = 1.4826  # 정규분포에서 MAD를 표준편차 척도로 맞추는 상수


def robust_center_scale(X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """특징별 중앙값과 척도(1.4826*MAD, 0이면 표준편차, 그것도 0이면 1)"""
    center = np.median(X, axis=0)
    scale = MAD_TO_STD * np.median(np.abs(X - center), axis=0)
    std = X.std(axis=0)
    scale = np.where(scale > 0, scale, std)
    scale = np.where(scale > 0, scale, 1.0)
    return center, scale


def z_scores(X: np.ndarray, center: np.ndarray, scale: np.ndarray) -> np.ndarray:
    return (np.abs(X - center) / scale).max(axis=1)


def _combined(if_scores, zs, if_threshold, z_threshold) -> np.ndarray:
    return np.maximum(if_scores / if_threshold, zs / z_threshold)


def fit_model(
    X: np.ndarray,
    threshold_quantile: float = DEFAULT_THRESHOLD_QUANTILE,
    random_state: int = 42,
) -> dict:
    model = IsolationForest(n_estimators=200, random_state=random_state)
    model.fit(X)
    train_if = -model.score_samples(X)
    center, scale = robust_center_scale(X)
    train_z = z_scores(X, center, scale)

    if_threshold = float(np.quantile(train_if, threshold_quantile))
    z_threshold = float(np.quantile(train_z, threshold_quantile))
    train_scores = _combined(train_if, train_z, if_threshold, z_threshold)
    return {
        "version": MODEL_VERSION,
        "model": model,
        "z_center": center,
        "z_scale": scale,
        "if_threshold": if_threshold,
        "z_threshold": z_threshold,
        "threshold": float(np.quantile(train_scores, threshold_quantile)),
        "threshold_quantile": threshold_quantile,
        "feature_names": list(FEATURE_NAMES),
    }


def score(bundle: dict, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(anomaly_score, is_anomaly) 반환"""
    if_scores = -bundle["model"].score_samples(X)
    if bundle.get("version", 1) == 1:
        scores = if_scores
    else:
        zs = z_scores(X, bundle["z_center"], bundle["z_scale"])
        scores = _combined(if_scores, zs, bundle["if_threshold"], bundle["z_threshold"])
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

    detail = ""
    if bundle.get("version", 1) >= 2:
        detail = f" [if_threshold={bundle['if_threshold']:.4f}, z_threshold={bundle['z_threshold']:.2f}]"
    return (
        f"v{bundle.get('version', 1)} rows={bundle['train_rows']} "
        f"range={fmt(bundle['train_start'])}~{fmt(bundle['train_end'])} "
        f"threshold={bundle['threshold']:.4f} (q={bundle['threshold_quantile']}){detail}"
    )
