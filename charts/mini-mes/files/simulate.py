"""
설비 시뮬레이터

한 번 전송(tick)은 "window-sec초 동안의 생산실적 1건"을 의미한다.
(실제 대기시간은 --interval, 데이터가 나타내는 가상 시간은 --window-sec)
생산 수량은 사이클타임과 구간 길이에서 계산하므로 물리적으로 말이 된다.

- 정상: 가동률 85~98%, 불량률 약 3%
- 이상(가끔): 사이클타임 2~3.5배 느려짐 + 불량률 20~40%  (--anomaly-rate, 구간마다 독립적으로 발생하는 급변 이상)
- 점진적 열화(선택, --drift-rate > 0일 때만): 설비별로 --drift-len 구간 동안 사이클타임이
  1.0배 -> --drift-max배, 불량률이 3% -> 10%로 선형 증가한 뒤 정상으로 복귀. 열화 구간 전체를 이상으로 라벨링.
  --drift-rate를 주지 않으면(기본 0) 기존과 완전히 같은 방식으로 데이터를 만든다.

사용법:
    python3 simulate.py --api-url http://localhost:8001 --interval 5

--labels-file을 주면 POST /production-logs 응답의 (equipment_id, ts, log_id)와
이 시뮬레이터가 이상으로 만들었는지(is_anomaly, anomaly_type=spike/drift)를 JSONL로 덧붙여 기록한다 (이상탐지 성능 평가용).
DB에는 이상 여부를 저장하지 않는다.
"""
import argparse
import json
import random
import time
from datetime import datetime, timezone

import requests

EQUIPMENT_IDS = ["EQ-001", "EQ-002", "EQ-003"]

# 설비별 정상 사이클타임(제품 1개당 초) 기준값
BASE_CYCLE_TIME = {"EQ-001": 12.0, "EQ-002": 15.0, "EQ-003": 8.0}

# 불량 유형 및 발생 확률 가중치 (정상 구간 vs ANOMALY 구간)
# ANOMALY 구간(사이클타임 급증)에서는 치수 불량(dimension_out)으로 쏠리게 한다.
DEFECT_TYPES = ["scratch", "dimension_out", "burr", "discoloration"]
NORMAL_DEFECT_WEIGHTS = [0.40, 0.20, 0.25, 0.15]
ANOMALY_DEFECT_WEIGHTS = [0.05, 0.75, 0.15, 0.05]

NORMAL_SEVERITY_WEIGHTS = [0.6, 0.3, 0.1]   # low, medium, high
ANOMALY_SEVERITY_WEIGHTS = [0.1, 0.3, 0.6]


def make_log(equipment_id: str, window_sec: int, cycle: float, util: float, defect_rate: float) -> dict:
    cycle = max(cycle, 0.1)
    total = max(0, round(window_sec * util / cycle))
    defect = sum(1 for _ in range(total) if random.random() < defect_rate)
    return {
        "equipment_id": equipment_id,
        "qty_good": total - defect,
        "qty_defect": defect,
        "cycle_time_sec": round(cycle, 2),
        "planned_time_sec": window_sec,
    }


def generate_normal_log(equipment_id: str, window_sec: int) -> dict:
    base = BASE_CYCLE_TIME[equipment_id]
    return make_log(
        equipment_id, window_sec,
        cycle=random.gauss(base, base * 0.05),
        util=random.uniform(0.85, 0.98),
        defect_rate=0.03,
    )


def generate_anomaly_log(equipment_id: str, window_sec: int) -> dict:
    """이상탐지 모델 검증용 - 사이클타임 급증 + 불량 급증"""
    base = BASE_CYCLE_TIME[equipment_id]
    return make_log(
        equipment_id, window_sec,
        cycle=base * random.uniform(2.0, 3.5),
        util=random.uniform(0.85, 0.98),
        defect_rate=random.uniform(0.2, 0.4),
    )


def generate_drift_log(equipment_id: str, window_sec: int, level: float, drift_max: float) -> dict:
    """점진적 열화 - level(0~1)에 비례해 사이클타임과 불량률이 서서히 증가"""
    base = BASE_CYCLE_TIME[equipment_id] * (1 + (drift_max - 1) * level)
    return make_log(
        equipment_id, window_sec,
        cycle=random.gauss(base, BASE_CYCLE_TIME[equipment_id] * 0.05),
        util=random.uniform(0.85, 0.98),
        defect_rate=0.03 + 0.07 * level,
    )


def send_quality_event(api_url: str, equipment_id: str, log_id: int, is_anomaly: bool) -> None:
    """불량이 발생한 생산실적 건에 한해, 해당 log_id를 가리키는 품질 이벤트를 함께 전송한다."""
    weights = ANOMALY_DEFECT_WEIGHTS if is_anomaly else NORMAL_DEFECT_WEIGHTS
    severity_weights = ANOMALY_SEVERITY_WEIGHTS if is_anomaly else NORMAL_SEVERITY_WEIGHTS
    payload = {
        "equipment_id": equipment_id,
        "production_log_id": log_id,
        "defect_type": random.choices(DEFECT_TYPES, weights=weights)[0],
        "severity": random.choices(["low", "medium", "high"], weights=severity_weights)[0],
    }
    try:
        res = requests.post(f"{api_url}/quality-events", json=payload, timeout=5)
        print(f"  -> quality-event {payload['defect_type']}/{payload['severity']} "
              f"(log_id={log_id}) -> {res.status_code}")
    except requests.RequestException as e:
        print(f"[error] {equipment_id} 품질 이벤트 전송 실패: {e}")


def write_label(labels_file: str, run_id: str, anomaly_rate: float, log: dict, is_anomaly: bool,
                anomaly_type: str = None, drift_rate: float = 0.0, drift_level: float = None) -> None:
    record = {
        "run_id": run_id,
        "anomaly_rate": anomaly_rate,
        "drift_rate": drift_rate,
        "equipment_id": log["equipment_id"],
        "ts": log["ts"],
        "log_id": log["log_id"],
        "is_anomaly": is_anomaly,
        "anomaly_type": anomaly_type,     # "spike" / "drift" / None
        "drift_level": drift_level,       # 열화 진행도 0~1 (drift일 때만)
    }
    with open(labels_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def run(api_url: str, interval: int, window_sec: int, anomaly_rate: float, max_ticks: int = 0,
        labels_file: str = "", drift_rate: float = 0.0, drift_len: int = 20, drift_max: float = 1.3):
    print(f"[simulate] {api_url} 로 {interval}초마다 전송 시작 "
          f"(1회 = {window_sec}초 분량, Ctrl+C로 종료)")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if labels_file:
        print(f"[simulate] 라벨 기록: {labels_file} (run_id={run_id})")
    drift_step = {eq_id: 0 for eq_id in EQUIPMENT_IDS}  # 0 = 열화 없음, 1..drift_len = 열화 진행 중
    tick = 0
    while True:
        for eq_id in EQUIPMENT_IDS:
            is_anomaly = random.random() < anomaly_rate
            drift_level = None
            if drift_rate > 0:
                if drift_step[eq_id] == 0 and random.random() < drift_rate:
                    drift_step[eq_id] = 1
                if drift_step[eq_id] > 0:
                    drift_level = drift_step[eq_id] / drift_len
                    drift_step[eq_id] = drift_step[eq_id] + 1 if drift_step[eq_id] < drift_len else 0

            if is_anomaly:  # 급변 이상이 열화보다 우선
                payload = generate_anomaly_log(eq_id, window_sec)
                anomaly_type = "spike"
            elif drift_level is not None:
                payload = generate_drift_log(eq_id, window_sec, drift_level, drift_max)
                anomaly_type = "drift"
            else:
                payload = generate_normal_log(eq_id, window_sec)
                anomaly_type = None
            try:
                res = requests.post(f"{api_url}/production-logs", json=payload, timeout=5)
                if anomaly_type == "spike":
                    tag = "ANOMALY"
                elif anomaly_type == "drift":
                    tag = f"DRIFT {drift_level:.0%}"
                else:
                    tag = "normal"
                print(f"[{datetime.now().isoformat(timespec='seconds')}] "
                      f"{eq_id} ({tag}) good={payload['qty_good']} "
                      f"defect={payload['qty_defect']} -> {res.status_code}")

                if res.status_code == 201 and labels_file:
                    write_label(labels_file, run_id, anomaly_rate, res.json(), anomaly_type is not None,
                                anomaly_type, drift_rate, drift_level)

                if res.status_code == 201 and payload["qty_defect"] > 0:
                    log_id = res.json()["log_id"]
                    send_quality_event(api_url, eq_id, log_id, is_anomaly)
            except requests.RequestException as e:
                print(f"[error] {eq_id} 전송 실패: {e}")

        tick += 1
        if max_ticks and tick >= max_ticks:
            return
        time.sleep(interval)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-url", default="http://localhost:8000")
    parser.add_argument("--interval", type=int, default=5, help="전송 주기(초, 실제 대기시간)")
    parser.add_argument("--window-sec", type=int, default=60, help="1회 전송이 나타내는 생산 구간(초)")
    parser.add_argument("--anomaly-rate", type=float, default=0.05, help="이상 데이터 발생 확률")
    parser.add_argument("--max-ticks", type=int, default=0, help="지정 횟수 후 종료(0=무한)")
    parser.add_argument("--labels-file", default="", help="라벨(JSONL) 기록 경로 (예: labels.jsonl, 생략 시 기록 안 함)")
    parser.add_argument("--drift-rate", type=float, default=0.0,
                        help="설비별로 매 구간 점진적 열화가 시작될 확률 (0이면 열화 없음, 기존 동작과 동일)")
    parser.add_argument("--drift-len", type=int, default=20, help="열화 1회의 길이(구간 수)")
    parser.add_argument("--drift-max", type=float, default=1.3, help="열화 끝의 사이클타임 배율")
    args = parser.parse_args()

    run(args.api_url, args.interval, args.window_sec, args.anomaly_rate, args.max_ticks, args.labels_file,
        args.drift_rate, args.drift_len, args.drift_max)