"""
production_log 한 건(= 한 생산 구간)에서 이상탐지용 특징을 뽑는다.

- cycle_time_sec : 제품 1개당 사이클타임(초)
- qty_total      : 구간당 생산 수량 (qty_good + qty_defect)
- defect_rate    : 구간 불량률 (qty_defect / qty_total, 생산 0건이면 0)
"""
from typing import Iterable, Sequence

import numpy as np

FEATURE_NAMES = ["cycle_time_sec", "qty_total", "defect_rate"]


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
