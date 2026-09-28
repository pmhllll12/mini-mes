"""
이상탐지 워커: INTERVAL_SEC마다 설비별로 아직 판정하지 않은 최근 생산실적을 추론해
anomaly_result에 (equipment_id, ts, anomaly_score, is_anomaly)를 기록한다.

- 모델은 MODEL_DIR/{equipment_id}.joblib (train.py가 생성). 파일이 바뀌면 재시작 없이 다시 읽는다.
- 모델이 없으면 죽지 않고 "모델 없음"을 로그로 남긴 뒤 다음 주기까지 대기한다.
- DB 오류가 나도 죽지 않고 다음 주기에 재시도한다.
- METRICS_PORT(기본 9100)의 /metrics로 Prometheus 메트릭을 노출한다.
"""
import logging
import os
import time

from prometheus_client import Counter, Gauge, start_http_server

from db import connect, fetch_unscored_rows, insert_results, list_equipment_ids
from features import extract_features
from model import describe, load_bundle, model_path, score

MODEL_DIR = os.getenv("MODEL_DIR", "/models")
INTERVAL_SEC = int(os.getenv("INTERVAL_SEC", "10"))
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "24"))
METRICS_PORT = int(os.getenv("METRICS_PORT", "9100"))

ANOMALY_SCORE = Gauge(
    "mes_anomaly_score", "직전 판정 주기에 판정한 생산실적 중 최대 이상 점수", ["equipment_id"]
)
ANOMALY_THRESHOLD = Gauge("mes_anomaly_threshold", "설비별 모델의 이상 판정 threshold", ["equipment_id"])
ANOMALY_MODEL_LOADED = Gauge("mes_anomaly_model_loaded", "모델 로드 여부 (1=있음, 0=없음)", ["equipment_id"])
ANOMALY_SCORED = Counter("mes_anomaly_scored_total", "판정한 생산실적 건수", ["equipment_id"])
ANOMALY_DETECTED = Counter("mes_anomaly_detected_total", "이상으로 판정한 건수", ["equipment_id"])

log = logging.getLogger("worker")


class ModelCache:
    """설비별 모델을 파일 mtime 기준으로 캐시 (재학습하면 자동으로 새 모델 사용)"""

    def __init__(self, model_dir: str):
        self.model_dir = model_dir
        self._cache: dict[str, tuple[float, dict]] = {}

    def get(self, equipment_id: str):
        path = model_path(self.model_dir, equipment_id)
        try:
            mtime = os.path.getmtime(path)
        except FileNotFoundError:
            self._cache.pop(equipment_id, None)
            return None
        cached = self._cache.get(equipment_id)
        if cached and cached[0] == mtime:
            return cached[1]
        bundle = load_bundle(self.model_dir, equipment_id)
        self._cache[equipment_id] = (mtime, bundle)
        log.info("%s: 모델 로드 %s", equipment_id, describe(bundle))
        return bundle


def run_once(conn, models: ModelCache) -> None:
    missing = []
    for eq_id in list_equipment_ids(conn):
        bundle = models.get(eq_id)
        if bundle is None:
            missing.append(eq_id)
            ANOMALY_MODEL_LOADED.labels(eq_id).set(0)
            continue
        ANOMALY_MODEL_LOADED.labels(eq_id).set(1)
        ANOMALY_THRESHOLD.labels(eq_id).set(bundle["threshold"])

        rows = fetch_unscored_rows(conn, eq_id, bundle["train_end"], LOOKBACK_HOURS)
        if not rows:
            continue
        X = extract_features(r[1:] for r in rows)
        scores, flags = score(bundle, X)
        inserted = insert_results(conn, eq_id, zip((r[0] for r in rows), scores, flags))
        ANOMALY_SCORE.labels(eq_id).set(float(scores.max()))
        ANOMALY_SCORED.labels(eq_id).inc(len(rows))
        ANOMALY_DETECTED.labels(eq_id).inc(int(flags.sum()))
        log.info("%s: %d건 판정, 이상 %d건 (최대 점수 %.4f, threshold %.4f)",
                 eq_id, inserted, int(flags.sum()), float(scores.max()), bundle["threshold"])

    if missing:
        log.warning("모델 없음: %s - train.py로 학습할 때까지 %d초마다 재확인",
                    ", ".join(missing), INTERVAL_SEC)


def main():
    log.info("워커 시작 (MODEL_DIR=%s, INTERVAL_SEC=%d, LOOKBACK_HOURS=%d, METRICS_PORT=%d)",
             MODEL_DIR, INTERVAL_SEC, LOOKBACK_HOURS, METRICS_PORT)
    start_http_server(METRICS_PORT)
    models = ModelCache(MODEL_DIR)
    conn = None
    while True:
        try:
            if conn is None or conn.closed:
                conn = connect()
            run_once(conn, models)
        except Exception:
            log.exception("주기 처리 실패 - 다음 주기에 재시도")
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
            conn = None
        time.sleep(INTERVAL_SEC)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    main()
