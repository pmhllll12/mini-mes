"""
production_log 한 건(= 한 생산 구간)에서 이상탐지용 특징을 뽑는다.

- cycle_time_sec : 제품 1개당 사이클타임(초)
- qty_total      : 구간당 생산 수량 (qty_good + qty_defect)
- defect_rate    : 구간 불량률 (qty_defect / qty_total, 생산 0건이면 0)

이동 구간 특징 (build_features의 window > 0일 때, 같은 설비의 시간순 생산실적 기준):
- rolling_median_cycle_time : 현재 포함 최근 window구간 사이클타임의 중앙값
    (중앙값이라 튀는 이상 1건이 뒤따르는 구간으로 번지는 영향이 작다)
- rolling_defect_rate       : 최근 window구간을 합친 불량률 (불량 합 / 생산 합)
    (구간당 생산량이 적어 불량률이 0%/25%로 오가는 설비에서 잡음을 줄인다)
앞쪽에 이력이 window보다 적으면 있는 구간만으로 계산한다.
"""
from typing import Iterable, Sequence

import numpy as np

FEATURE_NAMES = ["cycle_time_sec", "qty_total", "defect_rate"]
ROLLING_FEATURE_NAMES = ["rolling_median_cycle_time", "rolling_defect_rate"]


def feature_names(window: int = 0) -> list[str]:
    return FEATURE_NAMES + (ROLLING_FEATURE_NAMES if window > 0 else [])


def extract_features(rows: Iterable[Sequence]) -> np.ndarray:
    """rows: (cycle_time_sec, qty_good, qty_defect) 튜플들 -> (n, 3) 배열"""
    out = []
    for cycle_time_sec, qty_good, qty_defect in rows:
        qty_good = int(qty_good or 0)
        qty_defect = int(qty_defect or 0)
        total = qty_good + qty_defect
        defect_rate = qty_defect / total if total > 0 else 0.0
        out.append([float(cycle_time_sec), float(total), defect_rate])
    return np.asarray(out, dtype=float).reshape(-1, len(FEATURE_NAMES))


def build_features(rows: Iterable[Sequence], window: int = 0) -> np.ndarray:
    """rows: 같은 설비의 시간순 (cycle_time_sec, qty_good, qty_defect). window=0이면 extract_features와 같다."""
    rows = list(rows)
    base = extract_features(rows)
    if window <= 0:
        return base
    totals = base[:, 1]
    defects = np.asarray([int(r[2] or 0) for r in rows], dtype=float)
    rolling = np.zeros((len(rows), len(ROLLING_FEATURE_NAMES)))
    for i in range(len(rows)):
        lo = max(0, i - window + 1)
        total = totals[lo:i + 1].sum()
        rolling[i, 0] = np.median(base[lo:i + 1, 0])
        rolling[i, 1] = defects[lo:i + 1].sum() / total if total > 0 else 0.0
    return np.hstack([base, rolling])
