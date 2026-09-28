"""
설비별 Isolation Forest 학습.

반드시 "정상 데이터만" 있는 구간을 --since/--until로 지정한다.
(시뮬레이터를 --anomaly-rate 0 으로 돌리기 직전 시각을 --since로 넘기면 된다. README 참고)

사용법 (컨테이너 안):
    docker compose run --rm anomaly-worker python train.py --since 2026-09-28T03:00:00Z
"""
import argparse
import logging
import os
from datetime import datetime

from db import connect, fetch_training_rows, list_equipment_ids
from features import build_features
from model import DEFAULT_THRESHOLD_QUANTILE, describe, fit_model, save_bundle

MIN_TRAIN_ROWS = 50

log = logging.getLogger("train")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--since", required=True, type=datetime.fromisoformat,
                        help="학습 구간 시작 (ISO8601). 이 시각 이후는 정상 데이터만 있어야 한다")
    parser.add_argument("--until", type=datetime.fromisoformat, default=None,
                        help="학습 구간 끝 (ISO8601, 생략 시 현재까지)")
    parser.add_argument("--equipment-ids", nargs="*", default=None, help="생략 시 전체 설비")
    parser.add_argument("--threshold-quantile", type=float, default=DEFAULT_THRESHOLD_QUANTILE)
    parser.add_argument("--model-dir", default=os.getenv("MODEL_DIR", "/models"))
    parser.add_argument("--rolling-window", type=int, default=0,
                        help="이동 구간 특징(최근 N구간 사이클타임 중앙값·합산 불량률) 사용. 0이면 사용 안 함")
    args = parser.parse_args()

    conn = connect()
    try:
        equipment_ids = args.equipment_ids or list_equipment_ids(conn)
        for eq_id in equipment_ids:
            rows = fetch_training_rows(conn, eq_id, args.since, args.until)
            if len(rows) < MIN_TRAIN_ROWS:
                log.warning("%s: 학습 데이터 %d건 (< %d) - 건너뜀", eq_id, len(rows), MIN_TRAIN_ROWS)
                continue

            X = build_features((r[1:] for r in rows), args.rolling_window)
            bundle = fit_model(X, threshold_quantile=args.threshold_quantile, rolling_window=args.rolling_window)
            bundle.update(
                equipment_id=eq_id,
                train_rows=len(rows),
                train_start=rows[0][0],
                train_end=rows[-1][0],
            )
            path = save_bundle(bundle, args.model_dir, eq_id)
            log.info("%s: 학습 완료 %s -> %s", eq_id, describe(bundle), path)
    finally:
        conn.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    main()
