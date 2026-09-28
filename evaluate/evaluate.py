"""
이상탐지 성능 평가: 시뮬레이터 라벨(labels.jsonl)과 워커의 판정(anomaly_result)을
(equipment_id, ts)로 맞춰 precision / recall / F1을 설비별·전체로 계산한다.

- 평가 대상은 라벨 파일의 한 실행분(run_id, 기본: 가장 마지막 실행분)이다.
- 학습 데이터와 겹치지 않는지 확인하기 위해 설비별 모델의 학습 건수·시간 범위를 함께 출력하고,
  평가 데이터가 학습 구간(train_end) 이후인지 검사한다.
- 워커가 아직 판정하지 않은 라벨은 지표 계산에서 빼고 건수를 따로 보여준다.

DB·모델 볼륨에 접근해야 하므로 anomaly-worker 이미지 안에서 실행한다 (README 참고):
    docker compose run --rm -v "$PWD/evaluate:/eval" -v "$PWD/simulator:/sim:ro" \\
        anomaly-worker python /eval/evaluate.py --labels /sim/labels.jsonl
"""
import argparse
import json
import os
from collections import defaultdict
from datetime import datetime


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


def fmt_ts(ts: datetime) -> str:
    return ts.isoformat(timespec="seconds")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--labels", default="simulator/labels.jsonl")
    parser.add_argument("--run-id", default=None, help="평가할 시뮬레이터 실행분 (생략 시 마지막 실행분)")
    parser.add_argument("--model-dir", default=os.getenv("MODEL_DIR", "/models"))
    args = parser.parse_args()

    import joblib
    import psycopg2

    labels = load_labels(args.labels)
    runs = summarize_runs(labels)
    print("## 라벨 파일의 시뮬레이터 실행분")
    print("| run_id | anomaly_rate | 건수 | 이상 건수 | 시간 범위(UTC) |")
    print("|---|---|---|---|---|")
    for r in runs:
        print(f"| {r['run_id']} | {r['anomaly_rate']} | {r['count']} | {r['anomalies']} | "
              f"{fmt_ts(r['start'])} ~ {fmt_ts(r['end'])} |")

    run_id = args.run_id or runs[-1]["run_id"]
    eval_labels = [r for r in labels if r["run_id"] == run_id]
    print(f"\n평가 대상 run_id = {run_id}")

    by_eq = defaultdict(list)
    for rec in eval_labels:
        by_eq[rec["equipment_id"]].append(rec)

    conn = psycopg2.connect(os.environ["DATABASE_URL"])
    rows = []
    all_pairs = []
    print("\n## 학습 데이터 vs 평가 데이터")
    print("| 설비 | 학습 건수 | 학습 구간(UTC) | 평가 건수 | 평가 구간(UTC) | 겹침 | threshold |")
    print("|---|---|---|---|---|---|---|")
    for eq_id in sorted(by_eq):
        recs = by_eq[eq_id]
        bundle = joblib.load(os.path.join(args.model_dir, f"{eq_id}.joblib"))
        ev_start = min(r["ts"] for r in recs)
        ev_end = max(r["ts"] for r in recs)
        overlap = ev_start <= bundle["train_end"]
        print(f"| {eq_id} | {bundle['train_rows']} | {fmt_ts(bundle['train_start'])} ~ {fmt_ts(bundle['train_end'])} | "
              f"{len(recs)} | {fmt_ts(ev_start)} ~ {fmt_ts(ev_end)} | {'있음(!)' if overlap else '없음'} | "
              f"{bundle['threshold']:.4f} (q={bundle['threshold_quantile']}) |")

        preds = fetch_predictions(conn, eq_id, ev_start, ev_end)
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


if __name__ == "__main__":
    main()
