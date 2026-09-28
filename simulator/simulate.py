"""
설비 시뮬레이터

한 번 전송(tick)은 "window-sec초 동안의 생산실적 1건"을 의미한다.
(실제 대기시간은 --interval, 데이터가 나타내는 가상 시간은 --window-sec)
생산 수량은 사이클타임과 구간 길이에서 계산하므로 물리적으로 말이 된다.

- 정상: 가동률 85~98%, 불량률 약 3%
- 이상(가끔): 사이클타임 2~3.5배 느려짐 + 불량률 20~40%

사용법:
    python3 simulate.py --api-url http://localhost:8001 --interval 5
"""
import argparse
import random
import time
from datetime import datetime

import requests

EQUIPMENT_IDS = ["EQ-001", "EQ-002", "EQ-003"]

# 설비별 정상 사이클타임(제품 1개당 초) 기준값
BASE_CYCLE_TIME = {"EQ-001": 12.0, "EQ-002": 15.0, "EQ-003": 8.0}


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


def run(api_url: str, interval: int, window_sec: int, anomaly_rate: float, max_ticks: int = 0):
    print(f"[simulate] {api_url} 로 {interval}초마다 전송 시작 "
          f"(1회 = {window_sec}초 분량, Ctrl+C로 종료)")
    tick = 0
    while True:
        for eq_id in EQUIPMENT_IDS:
            is_anomaly = random.random() < anomaly_rate
            payload = (
                generate_anomaly_log(eq_id, window_sec)
                if is_anomaly
                else generate_normal_log(eq_id, window_sec)
            )
            try:
                res = requests.post(f"{api_url}/production-logs", json=payload, timeout=5)
                tag = "ANOMALY" if is_anomaly else "normal"
                print(f"[{datetime.now().isoformat(timespec='seconds')}] "
                      f"{eq_id} ({tag}) good={payload['qty_good']} "
                      f"defect={payload['qty_defect']} -> {res.status_code}")
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
    args = parser.parse_args()

    run(args.api_url, args.interval, args.window_sec, args.anomaly_rate, args.max_ticks)