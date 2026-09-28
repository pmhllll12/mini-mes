"""
이상탐지 워커: INTERVAL_SEC마다 설비별로 아직 판정하지 않은 최근 생산실적을 추론해
anomaly_result에 (equipment_id, ts, anomaly_score, is_anomaly)를 기록한다.

- 모델은 MODEL_DIR/{equipment_id}.joblib (train.py가 생성). 파일이 바뀌면 재시작 없이 다시 읽는다.
- 모델이 없으면 죽지 않고 "모델 없음"을 로그로 남긴 뒤 다음 주기까지 대기한다.
- DB 오류가 나도 죽지 않고 다음 주기에 재시도한다.
- METRICS_PORT(기본 9100)의 /metrics로 Prometheus 메트릭을 노출한다.

경보는 두 갈래로 나뉜다.
- 급변 경보: MODEL_DIR의 모델(v2, 이동 구간 특징 없음). 판정을 anomaly_result에 기록 (/anomalies로 조회).
- 열화 경보: DRIFT_MODEL_DIR의 모델(train.py --rolling-window, 이동 구간 특징 사용)로 같은 생산실적을 한 번 더 판정.
  anomaly_result 스키마에 탐지기 구분이 없어 DB에는 쓰지 않고, mes_drift_* 메트릭과 로그(경보 시작/해제)로만 내보낸다.
  열화 모델이 없으면 열화 감시만 건너뛴다 (급변 판정이 된 생산실적만 열화 판정 대상).
  급변 모델이 이상으로 판정한 생산실적은 열화로 보지 않는다 (급변 1건이 열화 경보까지 중복으로 울리지 않게).
"""
import logging
import os
import time

import numpy as np
from prometheus_client import Counter, Gauge, start_http_server

from db import connect, fetch_context_rows, fetch_unscored_rows, insert_results, list_equipment_ids
from features import build_features
from model import describe, load_bundle, model_path, score

MODEL_DIR = os.getenv("MODEL_DIR", "/models")
INTERVAL_SEC = int(os.getenv("INTERVAL_SEC", "10"))
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "24"))
METRICS_PORT = int(os.getenv("METRICS_PORT", "9100"))
DRIFT_MODEL_DIR = os.getenv("DRIFT_MODEL_DIR", os.path.join(MODEL_DIR, "drift"))

ANOMALY_SCORE = Gauge(
    "mes_anomaly_score", "직전 판정 주기에 판정한 생산실적 중 최대 이상 점수", ["equipment_id"]
)
ANOMALY_THRESHOLD = Gauge("mes_anomaly_threshold", "설비별 모델의 이상 판정 threshold", ["equipment_id"])
ANOMALY_MODEL_LOADED = Gauge("mes_anomaly_model_loaded", "모델 로드 여부 (1=있음, 0=없음)", ["equipment_id"])
ANOMALY_SCORED = Counter("mes_anomaly_scored_total", "판정한 생산실적 건수", ["equipment_id"])
ANOMALY_DETECTED = Counter("mes_anomaly_detected_total", "이상으로 판정한 건수", ["equipment_id"])

DRIFT_SCORE = Gauge("mes_drift_score", "열화 감시: 직전 판정 주기의 가장 최근 생산실적 열화 점수", ["equipment_id"])
DRIFT_THRESHOLD = Gauge("mes_drift_threshold", "열화 감시 모델의 threshold", ["equipment_id"])
DRIFT_MODEL_LOADED = Gauge("mes_drift_model_loaded", "열화 감시 모델 로드 여부 (1=있음, 0=없음)", ["equipment_id"])
DRIFT_ALARM = Gauge("mes_drift_alarm", "열화 경보 상태 (1=경보 중, 0=정상)", ["equipment_id"])
DRIFT_DETECTED = Counter("mes_drift_detected_total", "열화로 판정한 생산실적 건수", ["equipment_id"])
DRIFT_ALARM_RAISED = Counter("mes_drift_alarm_raised_total", "열화 경보 발생 횟수 (정상 -> 경보 전환)", ["equipment_id"])

log = logging.getLogger("worker")


class ModelCache:
    """설비별 모델을 파일 mtime 기준으로 캐시 (재학습하면 자동으로 새 모델 사용)"""

    def __init__(self, model_dir: str, label: str = ""):
        self.model_dir = model_dir
        self.label = label  # 로그 구분용 (예: "열화 ")
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
        log.info("%s: %s모델 로드 %s", equipment_id, self.label, describe(bundle))
        return bundle


def features_for(conn, equipment_id: str, rows, bundle: dict):
    """판정할 rows(ts, cycle, good, defect; 시간순)의 특징. 이동 구간 특징을 쓰는 모델이면 직전 이력을 붙여 계산한다."""
    window = bundle.get("rolling_window", 0)
    context = fetch_context_rows(conn, equipment_id, rows[0][0], window - 1) if window > 0 else []
    X = build_features((r[1:] for r in [*context, *rows]), window)
    return X[len(context):]


class DriftAlarm:
    """설비별 열화 경보 상태. 판정 결과를 시간순으로 넣으면 정상->경보, 경보->정상 전환을 돌려준다."""

    def __init__(self):
        self.active: dict[str, bool] = {}

    def update(self, equipment_id: str, flags) -> list[tuple[int, str]]:
        events = []
        state = self.active.get(equipment_id, False)
        for i, flag in enumerate(flags):
            flag = bool(flag)
            if flag != state:
                events.append((i, "raised" if flag else "cleared"))
                state = flag
        self.active[equipment_id] = state
        return events


def run_drift(conn, equipment_id: str, rows, drift_models: ModelCache, alarm: DriftAlarm, spike_flags=None) -> None:
    bundle = drift_models.get(equipment_id)
    if bundle is None:
        DRIFT_MODEL_LOADED.labels(equipment_id).set(0)
        return
    DRIFT_MODEL_LOADED.labels(equipment_id).set(1)
    DRIFT_THRESHOLD.labels(equipment_id).set(bundle["threshold"])

    scores, flags = score(bundle, features_for(conn, equipment_id, rows, bundle))
    if spike_flags is not None:
        flags = flags & ~np.asarray(spike_flags, dtype=bool)  # 급변으로 설명되는 구간은 열화 근거에서 제외
    DRIFT_SCORE.labels(equipment_id).set(float(scores[-1]))
    DRIFT_DETECTED.labels(equipment_id).inc(int(flags.sum()))
    for i, kind in alarm.update(equipment_id, flags):
        ts = rows[i][0].isoformat(timespec="seconds")
        if kind == "raised":
            DRIFT_ALARM_RAISED.labels(equipment_id).inc()
            log.warning("%s: 열화 경보 시작 (ts=%s, 점수 %.3f > threshold %.3f)",
                        equipment_id, ts, float(scores[i]), bundle["threshold"])
        else:
            log.info("%s: 열화 경보 해제 (ts=%s)", equipment_id, ts)
    DRIFT_ALARM.labels(equipment_id).set(int(alarm.active[equipment_id]))


def run_once(conn, models: ModelCache, drift_models: ModelCache = None, alarm: DriftAlarm = None) -> None:
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
        X = features_for(conn, eq_id, rows, bundle)
        scores, flags = score(bundle, X)
        inserted = insert_results(conn, eq_id, zip((r[0] for r in rows), scores, flags))
        ANOMALY_SCORE.labels(eq_id).set(float(scores.max()))
        ANOMALY_SCORED.labels(eq_id).inc(len(rows))
        ANOMALY_DETECTED.labels(eq_id).inc(int(flags.sum()))
        log.info("%s: %d건 판정, 이상 %d건 (최대 점수 %.4f, threshold %.4f)",
                 eq_id, inserted, int(flags.sum()), float(scores.max()), bundle["threshold"])

        if drift_models is not None:
            run_drift(conn, eq_id, rows, drift_models, alarm, spike_flags=flags)

    if missing:
        log.warning("모델 없음: %s - train.py로 학습할 때까지 %d초마다 재확인",
                    ", ".join(missing), INTERVAL_SEC)


def main():
    log.info("워커 시작 (MODEL_DIR=%s, DRIFT_MODEL_DIR=%s, INTERVAL_SEC=%d, LOOKBACK_HOURS=%d, METRICS_PORT=%d)",
             MODEL_DIR, DRIFT_MODEL_DIR, INTERVAL_SEC, LOOKBACK_HOURS, METRICS_PORT)
    start_http_server(METRICS_PORT)
    models = ModelCache(MODEL_DIR)
    drift_models = ModelCache(DRIFT_MODEL_DIR, label="열화 ")
    alarm = DriftAlarm()
    if not os.path.isdir(DRIFT_MODEL_DIR):
        log.info("열화 모델 없음 (%s) - 급변 판정만 수행. 열화 감시는 train.py --rolling-window 5 --model-dir %s로 학습",
                 DRIFT_MODEL_DIR, DRIFT_MODEL_DIR)
    conn = None
    while True:
        try:
            if conn is None or conn.closed:
                conn = connect()
            run_once(conn, models, drift_models, alarm)
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
