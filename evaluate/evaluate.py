"""
이상탐지 성능 평가: 시뮬레이터 라벨(labels.jsonl)과 워커의 판정(anomaly_result)을
(equipment_id, ts)로 맞춰 precision / recall / F1을 설비별·전체로 계산한다.

- 평가 대상은 라벨 파일의 한 실행분(run_id, 기본: 가장 마지막 실행분)이다.
- 학습 데이터와 겹치지 않는지 확인하기 위해 설비별 모델의 학습 건수·시간 범위를 함께 출력하고,
  평가 데이터가 학습 구간(train_end) 이후인지 검사한다.
- 워커가 아직 판정하지 않은 라벨은 지표 계산에서 빼고 건수를 따로 보여준다.
- --score-with DIR 을 주면 워커 판정(anomaly_result) 대신 DIR의 모델로 라벨 대상 생산실적을 직접 채점한다
  (DB에 기록하지 않음). 서로 다른 모델을 같은 평가 데이터로 비교할 때 쓴다.
- 라벨에 anomaly_type(spike/drift)이 있으면 유형별 recall도 보여준다 (예전 라벨은 spike로 간주).

DB·모델 볼륨에 접근해야 하므로 anomaly-worker 이미지 안에서 실행한다 (README 참고):
    docker compose run --rm -v "$PWD/evaluate:/eval" -v "$PWD/simulator:/sim:ro" \\
        anomaly-worker python /eval/evaluate.py --labels /sim/labels.jsonl
"""
import argparse
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

WORKER_APP_DIR = os.getenv("WORKER_APP_DIR", "/app")  # --score-with 때 워커 코드(features/model/worker) 위치


def compute_metrics(pairs) -> dict:
    """pairs: (실제 이상 여부, 예측 이상 여부) -> tp/fp/fn/tn, precision, recall, f1"""
    tp = fp = fn = tn = 0
    for actual, predicted in pairs:
        if actual and predicted:
            tp += 1
        elif predicted:
            fp += 1
        elif actual:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"n": tp + fp + fn + tn, "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall, "f1": f1}


def load_labels(path: str) -> list[dict]:
    labels = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rec = json.loads(line)
                rec["ts"] = datetime.fromisoformat(rec["ts"])
                labels.append(rec)
    return labels


def summarize_runs(labels: list[dict]) -> list[dict]:
    runs = defaultdict(list)
    for rec in labels:
        runs[rec["run_id"]].append(rec)
    return [
        {
            "run_id": run_id,
            "anomaly_rate": recs[0]["anomaly_rate"],
            "drift_rate": recs[0].get("drift_rate", 0.0),
            "count": len(recs),
            "anomalies": sum(r["is_anomaly"] for r in recs),
            "start": min(r["ts"] for r in recs),
            "end": max(r["ts"] for r in recs),
        }
        for run_id, recs in sorted(runs.items())
    ]


def fetch_predictions(conn, equipment_id: str, start: datetime, end: datetime) -> dict:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ts, is_anomaly FROM anomaly_result WHERE equipment_id = %s AND ts BETWEEN %s AND %s",
            (equipment_id, start, end),
        )
        return {ts: is_anomaly for ts, is_anomaly in cur.fetchall()}


def score_offline(conn, equipment_id: str, recs: list[dict], bundle: dict) -> dict:
    """라벨 대상 생산실적을 bundle 모델로 직접 채점 -> {ts: is_anomaly}. 이동 구간 특징의 이력도 워커와 같게 붙인다."""
    sys.path.insert(0, WORKER_APP_DIR)
    from model import score
    from worker import features_for

    with conn.cursor() as cur:
        cur.execute(
            "SELECT ts, cycle_time_sec, qty_good, qty_defect FROM production_log WHERE log_id = ANY(%s) ORDER BY ts",
            ([r["log_id"] for r in recs],),
        )
        rows = cur.fetchall()
    _, flags = score(bundle, features_for(conn, equipment_id, rows, bundle))
    return {row[0]: bool(flag) for row, flag in zip(rows, flags)}


def recall_by_type(labels: list[dict], preds: dict) -> dict:
    """이상 유형별 (잡은 수, 전체 수). preds: {(equipment_id, ts): is_anomaly}"""
    out = defaultdict(lambda: [0, 0])
    for r in labels:
        key = (r["equipment_id"], r["ts"])
        if r["is_anomaly"] and key in preds:
            kind = r.get("anomaly_type") or "spike"
            out[kind][1] += 1
            out[kind][0] += int(preds[key])
    return {k: tuple(v) for k, v in sorted(out.items())}


def fmt_ts(ts: datetime) -> str:
    return ts.isoformat(timespec="seconds")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", default="simulator/labels.jsonl")
    parser.add_argument("--run-id", default=None, help="평가할 시뮬레이터 실행분 (생략 시 마지막 실행분)")
    parser.add_argument("--model-dir", default=os.getenv("MODEL_DIR", "/models"))
    parser.add_argument("--score-with", default=None,
                        help="워커 판정 대신 이 디렉터리의 모델로 직접 채점 (DB 기록 없음)")
    args = parser.parse_args()
    model_dir = args.score_with or args.model_dir

    import joblib
    import psycopg2

    labels = load_labels(args.labels)
    runs = summarize_runs(labels)
    print("## 라벨 파일의 시뮬레이터 실행분")
    print("| run_id | anomaly_rate | drift_rate | 건수 | 이상 건수 | 시간 범위(UTC) |")
    print("|---|---|---|---|---|---|")
    for r in runs:
        print(f"| {r['run_id']} | {r['anomaly_rate']} | {r['drift_rate']} | {r['count']} | {r['anomalies']} | "
              f"{fmt_ts(r['start'])} ~ {fmt_ts(r['end'])} |")

    run_id = args.run_id or runs[-1]["run_id"]
    eval_labels = [r for r in labels if r["run_id"] == run_id]
    print(f"\n평가 대상 run_id = {run_id}")
    print(f"판정 출처 = {'모델 직접 채점: ' + model_dir if args.score_with else '워커 기록(anomaly_result)'}")

    by_eq = defaultdict(list)
    for rec in eval_labels:
        by_eq[rec["equipment_id"]].append(rec)

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    rows = []
    all_pairs = []
    all_preds = {}
    print("\n## 학습 데이터 vs 평가 데이터")
    print("| 설비 | 학습 건수 | 학습 구간(UTC) | 평가 건수 | 평가 구간(UTC) | 겹침 | threshold |")
    print("|---|---|---|---|---|---|---|")
    for eq_id in sorted(by_eq):
        recs = by_eq[eq_id]
        bundle = joblib.load(os.path.join(model_dir, f"{eq_id}.joblib"))
        ev_start = min(r["ts"] for r in recs)
        ev_end = max(r["ts"] for r in recs)
        overlap = ev_start <= bundle["train_end"]
        print(f"| {eq_id} | {bundle['train_rows']} | {fmt_ts(bundle['train_start'])} ~ {fmt_ts(bundle['train_end'])} | "
              f"{len(recs)} | {fmt_ts(ev_start)} ~ {fmt_ts(ev_end)} | {'있음(!)' if overlap else '없음'} | "
              f"{bundle['threshold']:.4f} (q={bundle['threshold_quantile']}) |")

        if args.score_with:
            preds = score_offline(conn, eq_id, recs, bundle)
        else:
            preds = fetch_predictions(conn, eq_id, ev_start, ev_end)
        all_preds.update({(eq_id, ts): p for ts, p in preds.items()})
        pairs = [(r["is_anomaly"], preds[r["ts"]]) for r in recs if r["ts"] in preds]
        all_pairs += pairs
        rows.append((eq_id, len(recs) - len(pairs), sum(r["is_anomaly"] for r in recs) / len(recs),
                     compute_metrics(pairs)))
    conn.close()

    total_unscored = sum(r[1] for r in rows)
    rows.append(("전체", total_unscored, sum(r["is_anomaly"] for r in eval_labels) / len(eval_labels),
                 compute_metrics(all_pairs)))

    print("\n## 성능")
    print("| 설비 | 평가 건수 | 미판정 | 이상 비율 | TP | FP | FN | TN | Precision | Recall | F1 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|")
    for eq_id, unscored, rate, m in rows:
        print(f"| {eq_id} | {m['n']} | {unscored} | {rate:.1%} | {m['tp']} | {m['fp']} | {m['fn']} | {m['tn']} | "
              f"{m['precision']:.3f} | {m['recall']:.3f} | {m['f1']:.3f} |")

    print("\n## 이상 유형별 recall")
    print("| 유형 | 잡은 수 / 전체 | Recall |")
    print("|---|---|---|")
    for kind, (hit, total) in recall_by_type(eval_labels, all_preds).items():
        print(f"| {kind} | {hit} / {total} | {hit / total:.3f} |")


if __name__ == "__main__":
    main()
