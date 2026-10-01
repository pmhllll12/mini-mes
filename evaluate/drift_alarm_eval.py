"""
열화 경보 평가: 시뮬레이터 라벨의 열화 에피소드를 기준으로 경보 정책을 오프라인으로 재생해 비교한다.

- 평가 대상은 라벨 파일의 한 실행분(run_id, 기본: 마지막 실행분). 학습 구간과 겹치면 중단한다.
- 판정은 워커와 같은 코드로 다시 계산한다: 급변 = MODEL_DIR(v2), 열화 = DRIFT_MODEL_DIR(이동 구간),
  경보 = worker.DriftAlarm. 급변 판정 구간은 열화 판단 보류(None) - 워커와 같다. DB에는 쓰지 않는다.
- 정책은 --policy 이름=raise/clear[,k=..,h=..,clear_z=..] 로 여러 개 비교한다 (예: base=2/3, cz=2/2,clear_z=1).

지표 (에피소드 = 라벨의 drift_level이 1/len부터 이어지는 구간):
  경보 시작 수와 원인 분류(열화 중 / 급변 직후 4구간 / 열화 직후 4구간 / 그 외 정상),
  에피소드 감지율, 에피소드당 경보 수, 첫 경보 위치(완주 에피소드, 진행 구간 번호 중앙값),
  도중 해제된 에피소드, 정상인데 경보 중인 구간 수, 열화 종료 후 해제까지 걸린 구간 수,
  정상 1000구간당 오경보(그 외 정상 원인).

DB·모델 볼륨에 접근해야 하므로 anomaly-worker 이미지 안에서 실행한다:
    docker compose run --rm -v "$PWD/evaluate:/eval" -v "$PWD/simulator:/sim:ro" \\
        anomaly-worker python /eval/drift_alarm_eval.py --labels /sim/labels.jsonl --policy base=2/3
"""
import argparse
import os
import statistics
import sys
from collections import defaultdict

WORKER_APP_DIR = os.getenv("WORKER_APP_DIR", "/app")
AFTER = 4  # "직후"로 볼 구간 수


def parse_policy(text: str) -> tuple[str, dict]:
    """'cz=2/2,clear_z=1,k=0.5,h=5' -> ('cz', {raise_after, clear_after, clear_z, cusum_k, cusum_h})"""
    name, _, spec = text.partition("=")
    parts = spec.split(",")
    raise_after, clear_after = (int(x) for x in parts[0].split("/"))
    params = {"raise_after": raise_after, "clear_after": clear_after}
    keys = {"k": "cusum_k", "h": "cusum_h", "clear_z": "clear_z"}
    for part in parts[1:]:
        key, _, value = part.partition("=")
        if key not in keys:
            raise ValueError(f"알 수 없는 정책 옵션: {key} (k, h, clear_z)")
        params[keys[key]] = float(value)
    return name, params


def find_episodes(levels: list) -> list[tuple[int, int, bool]]:
    """levels: 구간별 drift_level(없으면 None). -> (시작, 끝(포함), 완주 여부) 목록.
    진행도가 다시 작아지면 새 에피소드. 완주 = 첫 구간부터 시작해 진행도 1.0까지 도달."""
    episodes, start = [], None
    for i, level in enumerate(levels):
        if level is None:
            if start is not None:
                episodes.append((start, i - 1))
                start = None
            continue
        if start is None:
            start = i
        elif level <= levels[i - 1]:
            episodes.append((start, i - 1))
            start = i
    if start is not None:
        episodes.append((start, len(levels) - 1))
    first_level = min((lv for lv in levels if lv is not None), default=None)
    return [(s, e, levels[s] == first_level and levels[e] >= 1.0 - 1e-9) for s, e in episodes]


def analyze(levels: list, spikes: list, active: list, events: list) -> dict:
    """한 설비의 구간별 라벨(levels, spikes)과 재생 결과(active, events=(i, raised/cleared))로 지표를 센다"""
    n = len(levels)
    in_drift = [lv is not None for lv in levels]
    episodes = find_episodes(levels)
    episode_end_before = [None] * n  # 직전에 끝난 에피소드의 끝 위치
    for _, e, _ in episodes:
        for j in range(e + 1, min(n, e + 1 + AFTER)):
            if not in_drift[j]:
                episode_end_before[j] = e

    causes = defaultdict(int)
    for i, kind in events:
        if kind != "raised":
            continue
        if in_drift[i]:
            causes["drift"] += 1
        elif any(spikes[max(0, i - AFTER):i]):
            causes["after_spike"] += 1
        elif episode_end_before[i] is not None:
            causes["after_drift"] += 1
        else:
            causes["normal"] += 1

    detected = raises_in_episodes = mid_clears = 0
    first_alarm, clear_lag = [], []
    for s, e, complete in episodes:
        span = range(s, e + 1)
        if any(active[i] for i in span):
            detected += 1
            if complete:
                first_alarm.append(next(i for i in span if active[i]) - s + 1)
        raises_in_episodes += sum(1 for i, kind in events if kind == "raised" and s <= i <= e)
        if any(kind == "cleared" and s <= i <= e for i, kind in events):
            mid_clears += 1
        if complete and active[e]:
            lag = 0
            for j in range(e + 1, n):
                if not active[j] or in_drift[j]:
                    break
                lag += 1
            else:
                lag = None  # 실행이 끝날 때까지 해제되지 않음 - 집계에서 제외
            if lag is not None:
                clear_lag.append(lag)

    normal_intervals = sum(1 for i in range(n) if not in_drift[i] and not spikes[i])
    return {
        "raised": sum(causes.values()), "causes": dict(causes),
        "episodes": len(episodes), "complete": sum(1 for *_, c in episodes if c),
        "detected": detected, "raises_in_episodes": raises_in_episodes, "mid_clears": mid_clears,
        "first_alarm": first_alarm, "clear_lag": clear_lag,
        "active_normal": sum(1 for i in range(n) if active[i] and not in_drift[i]),
        "normal_intervals": normal_intervals,
    }


def merge(results: list[dict]) -> dict:
    total = defaultdict(int)
    causes = defaultdict(int)
    first_alarm, clear_lag = [], []
    for r in results:
        for k, v in r.items():
            if isinstance(v, int):
                total[k] += v
        for k, v in r["causes"].items():
            causes[k] += v
        first_alarm += r["first_alarm"]
        clear_lag += r["clear_lag"]
    return {**total, "causes": dict(causes), "first_alarm": first_alarm, "clear_lag": clear_lag}


def replay(alarm_cls, params: dict, equipment_id: str, spike_flags, drift_flags, zs):
    alarm = alarm_cls(**params)
    judgements = [None if s else bool(f) for f, s in zip(drift_flags, spike_flags)]
    events = alarm.update(equipment_id, judgements, zs if alarm.uses_cycle_z else None)
    active, state, k = [], False, 0
    for i in range(len(judgements)):
        while k < len(events) and events[k][0] == i:
            state = events[k][1] == "raised"
            k += 1
        active.append(state)
    return active, events


def fmt_row(name: str, m: dict) -> str:
    c = m["causes"]
    per_episode = m["raises_in_episodes"] / m["detected"] if m["detected"] else 0.0
    first = statistics.median(m["first_alarm"]) if m["first_alarm"] else float("nan")
    lag = m["clear_lag"]
    lag_text = f"{statistics.median(lag):.0f} / {statistics.mean(lag):.1f}" if lag else "-"
    false_rate = 1000 * c.get("normal", 0) / m["normal_intervals"] if m["normal_intervals"] else 0.0
    return (f"| {name} | {m['raised']} | {c.get('drift', 0)} | {c.get('after_spike', 0)} | {c.get('after_drift', 0)} "
            f"| {c.get('normal', 0)} | {m['detected']}/{m['episodes']} | {per_episode:.2f} | {first:.0f}/20 "
            f"| {m['mid_clears']} | {m['active_normal']} | {lag_text} | {false_rate:.1f} |")


HEADER = ("| 정책 | 경보 시작 | 열화 중 | 급변 직후 4구간 | 열화 직후 4구간 | 그 외 정상 | 에피소드 감지 | 에피소드당 경보 "
          "| 첫 경보(중앙값) | 도중 해제 | 정상인데 경보 중 | 종료 후 해제까지(중앙값/평균) | 정상 1000구간당 오경보 |\n"
          "|---|---|---|---|---|---|---|---|---|---|---|---|---|")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", default="simulator/labels.jsonl")
    parser.add_argument("--run-id", default=None, help="평가할 실행분 (생략 시 마지막 실행분)")
    parser.add_argument("--model-dir", default=os.getenv("MODEL_DIR", "/models"))
    parser.add_argument("--drift-model-dir", default=os.getenv("DRIFT_MODEL_DIR", "/models/drift"))
    parser.add_argument("--policy", action="append", required=True, help="이름=raise/clear[,k=..,h=..,clear_z=..]")
    args = parser.parse_args()

    sys.path.insert(0, WORKER_APP_DIR)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from db import connect
    from evaluate import load_labels, summarize_runs
    from features import build_features
    from model import load_bundle, score
    from worker import DriftAlarm, cycle_z, features_for

    policies = [parse_policy(p) for p in args.policy]
    labels = load_labels(args.labels)
    runs = summarize_runs(labels)
    run_id = args.run_id or runs[-1]["run_id"]
    run = next(r for r in runs if r["run_id"] == run_id)
    print(f"실행분 {run_id}: {run['count']}건, 이상 {run['anomalies']}건 (anomaly_rate={run['anomaly_rate']}, "
          f"drift_rate={run['drift_rate']}), {run['start']:%Y-%m-%d %H:%M:%S} ~ {run['end']:%H:%M:%S}")

    by_eq = defaultdict(list)
    for rec in labels:
        if rec["run_id"] == run_id:
            by_eq[rec["equipment_id"]].append(rec)

    results = defaultdict(list)
    conn = connect()
    try:
        for eq_id, recs in sorted(by_eq.items()):
            recs.sort(key=lambda r: r["ts"])
            spike_bundle = load_bundle(args.model_dir, eq_id)
            drift_bundle = load_bundle(args.drift_model_dir, eq_id)
            for b in (spike_bundle, drift_bundle):
                if recs[0]["ts"] <= b["train_end"]:
                    sys.exit(f"{eq_id}: 평가 데이터가 학습 구간(~{b['train_end']})과 겹칩니다")
            with conn.cursor() as cur:
                cur.execute("SELECT ts, cycle_time_sec, qty_good, qty_defect FROM production_log "
                            "WHERE equipment_id = %s AND ts = ANY(%s) ORDER BY ts", (eq_id, [r["ts"] for r in recs]))
                rows = cur.fetchall()
            if len(rows) != len(recs):
                sys.exit(f"{eq_id}: 라벨 {len(recs)}건 중 DB에서 {len(rows)}건만 찾음")
            _, spike_flags = score(spike_bundle, build_features(r[1:] for r in rows))
            X = features_for(conn, eq_id, rows, drift_bundle)
            _, drift_flags = score(drift_bundle, X)
            zs = cycle_z(drift_bundle, X)
            levels = [r.get("drift_level") for r in recs]
            spikes = [r.get("anomaly_type") == "spike" for r in recs]
            for name, params in policies:
                active, events = replay(DriftAlarm, params, eq_id, spike_flags, drift_flags, zs)
                results[name].append(analyze(levels, spikes, active, events))
    finally:
        conn.close()

    print(HEADER)
    for name, _ in policies:
        print(fmt_row(name, merge(results[name])))


if __name__ == "__main__":
    main()
