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
  판정 건별 결과는 DB에 쓰지 않고(anomaly_result에 탐지기 구분이 없음) mes_drift_* 메트릭으로 내보내며,
  경보 시작/해제는 drift_alarm 테이블에 이력으로 남긴다 (/drift-alarms로 조회). 워커가 재시작되면
  drift_alarm의 진행 중 경보로 경보 상태를 복원한다 (연속 판정 횟수는 0부터 다시 셈).
  열화 모델이 없으면 열화 감시만 건너뛴다 (급변 판정이 된 생산실적만 열화 판정 대상).
  급변 모델이 이상으로 판정한 생산실적은 열화 경보 판단을 보류한다 (경보를 켜는 근거도, 끄는 근거도 아님).
  급변 1건이 열화 경보까지 중복으로 울리지 않게 하고, 급변 탐지기도 잡을 만큼 진행된 열화 후반에
  열화 경보가 도중에 꺼지지 않게 하기 위함.
  경보는 열화 판정 DRIFT_RAISE_AFTER구간 연속이면 시작, 정상 판정 DRIFT_CLEAR_AFTER구간 연속이면 해제한다
  (threshold 근처에서 켜졌다 꺼지기를 반복하는 깜빡임 완화).
"""
import logging
import os
import time

import numpy as np
from prometheus_client import Counter, Gauge, start_http_server

from db import (
    close_drift_alarm,
    connect,
    fetch_context_rows,
    fetch_unscored_rows,
    insert_results,
    list_equipment_ids,
    load_open_drift_alarms,
    open_drift_alarm,
)
from features import build_features
from model import describe, load_bundle, model_path, score

MODEL_DIR = os.getenv("MODEL_DIR", "/models")
INTERVAL_SEC = int(os.getenv("INTERVAL_SEC", "10"))
LOOKBACK_HOURS = int(os.getenv("LOOKBACK_HOURS", "24"))
METRICS_PORT = int(os.getenv("METRICS_PORT", "9100"))
DRIFT_MODEL_DIR = os.getenv("DRIFT_MODEL_DIR", os.path.join(MODEL_DIR, "drift"))
DRIFT_RAISE_AFTER = int(os.getenv("DRIFT_RAISE_AFTER", "2"))
DRIFT_CLEAR_AFTER = int(os.getenv("DRIFT_CLEAR_AFTER", "3"))

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
    """설비별 열화 경보 상태 (히스테리시스).

    판정 결과(True=열화, False=정상, None=판단 보류)를 시간순으로 넣으면 정상->경보, 경보->정상 전환을 돌려준다.
    열화 판정이 raise_after구간 연속이면 경보 시작, 정상 판정이 clear_after구간 연속이면 해제.
    None은 건너뛴다 (연속 횟수를 늘리지도 끊지도 않음).
    연속 횟수는 판정 주기를 넘어 이어진다. raise_after=clear_after=1이면 판정을 그대로 따른다.
    """

    def __init__(self, raise_after: int = 1, clear_after: int = 1):
        self.raise_after = raise_after
        self.clear_after = clear_after
        self.active: dict[str, bool] = {}
        self._streak: dict[str, int] = {}  # 현재 상태와 반대인 판정이 연속된 횟수

    def restore(self, active_equipment_ids) -> None:
        """재시작 시 DB(drift_alarm)의 진행 중 경보로 상태를 되살린다"""
        for equipment_id in active_equipment_ids:
            self.active[equipment_id] = True

    def update(self, equipment_id: str, flags) -> list[tuple[int, str]]:
        events = []
        state = self.active.get(equipment_id, False)
        streak = self._streak.get(equipment_id, 0)
        for i, flag in enumerate(flags):
            if flag is None:
                continue
            if bool(flag) == state:
                streak = 0
                continue
            streak += 1
            if streak >= (self.clear_after if state else self.raise_after):
                state = not state
                streak = 0
                events.append((i, "raised" if state else "cleared"))
        self.active[equipment_id] = state
        self._streak[equipment_id] = streak
        return events


def run_drift(conn, equipment_id: str, rows, drift_models: ModelCache, alarm: DriftAlarm, spike_flags=None) -> None:
    bundle = drift_models.get(equipment_id)
    if bundle is None:
        DRIFT_MODEL_LOADED.labels(equipment_id).set(0)
        return
    DRIFT_MODEL_LOADED.labels(equipment_id).set(1)
    DRIFT_THRESHOLD.labels(equipment_id).set(bundle["threshold"])

    scores, flags = score(bundle, features_for(conn, equipment_id, rows, bundle))
    spike = np.zeros(len(rows), dtype=bool) if spike_flags is None else np.asarray(spike_flags, dtype=bool)
    DRIFT_SCORE.labels(equipment_id).set(float(scores[-1]))
    DRIFT_DETECTED.labels(equipment_id).inc(int((flags & ~spike).sum()))
    # 급변으로 판정된 구간은 판단 보류(None)
    judgements = [None if s else bool(f) for f, s in zip(flags, spike)]
    for i, kind in alarm.update(equipment_id, judgements):
        ts = rows[i][0]
        if kind == "raised":
            open_drift_alarm(conn, equipment_id, ts, float(scores[i]), bundle["threshold"])
            DRIFT_ALARM_RAISED.labels(equipment_id).inc()
            log.warning("%s: 열화 경보 시작 (ts=%s, 점수 %.3f > threshold %.3f)",
                        equipment_id, ts.isoformat(timespec="seconds"), float(scores[i]), bundle["threshold"])
        else:
            close_drift_alarm(conn, equipment_id, ts)
            log.info("%s: 열화 경보 해제 (ts=%s)", equipment_id, ts.isoformat(timespec="seconds"))
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
    alarm = DriftAlarm(DRIFT_RAISE_AFTER, DRIFT_CLEAR_AFTER)
    log.info("열화 경보: %d구간 연속 열화 판정 시 시작, %d구간 연속 정상 시 해제", DRIFT_RAISE_AFTER, DRIFT_CLEAR_AFTER)
    if not os.path.isdir(DRIFT_MODEL_DIR):
        log.info("열화 모델 없음 (%s) - 급변 판정만 수행. 열화 감시는 train.py --rolling-window 5 --model-dir %s로 학습",
                 DRIFT_MODEL_DIR, DRIFT_MODEL_DIR)
    conn = None
    restored = False
    while True:
        try:
            if conn is None or conn.closed:
                conn = connect()
            if not restored:
                active = load_open_drift_alarms(conn)
                alarm.restore(active)
                restored = True
                if active:
                    log.info("열화 경보 상태 복원 (진행 중): %s", ", ".join(active))
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
