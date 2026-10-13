"""
정밀가공 품질(KAMP) 데이터에 이상탐지 모델을 오프라인으로 적용해, 불합격을 얼마나 이상으로 잡는지 평가한다.
설비 이상탐지 워커·DB 판정 결과는 건드리지 않는다 (모델 코드만 재사용, DB에 기록하지 않음).

모델: 워커와 같은 v2 (Isolation Forest + robust z-score, anomaly-worker/model.py fit_model/score)
특징: 정밀가공 센서 열 중 상수 열을 뺀 machining_quality.FEATURE_COLUMNS

SerialNo 중복 쌍 처리 (같은 SerialNo·시각에 합격/불합격 두 행, 센서 값은 서로 다름. 불합격 99건이 전부 쌍에 속함):
- 학습: 중복이 아닌 합격 행만, 그중 일부(--holdout)는 평가용 정상으로 떼어 둔다 (정상 데이터로만 학습)
- 기준값: 학습 데이터 점수의 --quantile 분위수 (불합격 데이터로 조정하지 않음)
- 평가: 이상 = 쌍의 불합격 행, 정상 = 떼어 둔 합격 행 (+ 옵션 비교: 쌍의 합격 행을 정상에 포함/제외)
- 쌍 비교: 각 쌍에서 불합격 행 점수가 합격 행보다 높은 비율 (SerialNo·시각이 같은 행끼리라 조건이 통제됨)
데이터가 작아(정상 887건) 학습/평가 분할을 시드 여러 개로 반복해 평균과 범위를 보여준다.

DB·워커 코드가 필요하므로 anomaly-worker 이미지 안에서 실행한다 (api 코드는 열 정의용으로 읽기 전용 마운트):
    docker compose run --rm -v "$PWD/evaluate:/eval" -v "$PWD/api:/api:ro" \\
        anomaly-worker python /eval/machining_anomaly_eval.py
"""
import argparse
import json
import os
import sys

import numpy as np

WORKER_APP_DIR = os.getenv("WORKER_APP_DIR", "/app")  # 워커 코드(model.py) 위치
MQ_APP_DIR = os.getenv("MQ_APP_DIR", "/api")          # api 코드(machining_quality.py) 위치

NEGATIVE_SETS = {
    "with_pair_pass": "정상 = 떼어 둔 합격 + 쌍의 합격 행",
    "without_pair_pass": "정상 = 떼어 둔 합격만",
}


def _worker_model():
    if WORKER_APP_DIR not in sys.path:
        sys.path.insert(0, WORKER_APP_DIR)
    import model
    return model


def load_records(database_url: str, time_basis: str = "source") -> dict:
    """DB의 정밀가공 행 전체 -> 특징 행렬과 메타 정보 (재생 중이어도 아직 도착하지 않은 행까지 모두 사용)"""
    import psycopg2

    if MQ_APP_DIR not in sys.path:
        sys.path.insert(0, MQ_APP_DIR)
    import machining_quality as mq

    cols = ", ".join(mq.FEATURE_COLUMNS)
    with psycopg2.connect(database_url) as conn, conn.cursor() as cur:
        cur.execute(
            f"SELECT serial_no, source_received_at, pass_or_fail, is_serial_conflict, {cols} "
            "FROM machining_quality_record ORDER BY source_received_at, record_id"
        )
        rows = cur.fetchall()
    if not rows:
        raise SystemExit("machining_quality_record가 비어 있음 - 먼저 api/load_machining_quality.py로 적재")
    return {
        "feature_names": list(mq.FEATURE_COLUMNS),
        "pair_key": [f"{r[0]}|{r[1].isoformat()}" for r in rows],
        "label": np.array([r[2] for r in rows], dtype=int),
        "conflict": np.array([r[3] for r in rows], dtype=bool),
        "X": np.array([r[4:] for r in rows], dtype=float),
    }


def split_groups(label: np.ndarray, conflict: np.ndarray, holdout: float, seed: int) -> dict:
    """행 번호를 학습 정상 / 평가 정상 / 쌍의 합격 / 쌍의 불합격으로 나눈다"""
    clean = np.flatnonzero(~conflict & (label == 0))
    if np.any(~conflict & (label == 1)):
        # 지금 데이터에는 없음. 생기면 평가 이상에 넣는다 (학습에는 절대 넣지 않음)
        print("참고: 중복이 아닌 불합격 행이 있어 평가 이상에 포함", file=sys.stderr)
    rng = np.random.default_rng(seed)
    shuffled = rng.permutation(clean)
    n_hold = int(round(len(clean) * holdout))
    return {
        "train": np.sort(shuffled[n_hold:]),
        "holdout": np.sort(shuffled[:n_hold]),
        "pair_pass": np.flatnonzero(conflict & (label == 0)),
        "fail": np.flatnonzero(label == 1),
    }


def compute_metrics(scores_pos: np.ndarray, scores_neg: np.ndarray, threshold: float) -> dict:
    """이상(불합격) / 정상 점수 -> 기준값 판정 지표 + 기준값과 무관한 AUC(이상 점수가 정상보다 높을 확률)"""
    tp = int((scores_pos > threshold).sum())
    fn = len(scores_pos) - tp
    fp = int((scores_neg > threshold).sum())
    tn = len(scores_neg) - fp
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    fpr = fp / (fp + tn) if fp + tn else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": precision, "recall": recall,
            "f1": f1, "fpr": fpr, "auc": auc(scores_pos, scores_neg)}


def auc(scores_pos: np.ndarray, scores_neg: np.ndarray) -> float | None:
    """Mann-Whitney 방식 ROC AUC (동점은 0.5)"""
    if len(scores_pos) == 0 or len(scores_neg) == 0:
        return None
    diff = scores_pos[:, None] - scores_neg[None, :]
    return float(((diff > 0).sum() + 0.5 * (diff == 0).sum()) / diff.size)


def pair_win_rate(scores: np.ndarray, label: np.ndarray, pair_key: list[str]) -> dict:
    """같은 SerialNo·시각 쌍에서 불합격 행 점수 > 합격 행 점수인 비율"""
    by_key: dict[str, dict[int, float]] = {}
    for i, key in enumerate(pair_key):
        by_key.setdefault(key, {})[int(label[i])] = float(scores[i])
    pairs = [(v[1], v[0]) for v in by_key.values() if 0 in v and 1 in v]
    wins = sum(1 for f, p in pairs if f > p)
    ties = sum(1 for f, p in pairs if f == p)
    return {"pairs": len(pairs), "fail_higher": wins, "ties": ties,
            "rate": (wins + 0.5 * ties) / len(pairs) if pairs else None}


def evaluate_seed(data: dict, holdout: float, quantile: float, seed: int) -> dict:
    model = _worker_model()
    groups = split_groups(data["label"], data["conflict"], holdout, seed)
    bundle = model.fit_model(data["X"][groups["train"]], threshold_quantile=quantile, random_state=seed)
    scores, _ = model.score(bundle, data["X"])
    threshold = bundle["threshold"]

    pos = scores[groups["fail"]]
    negatives = {
        "with_pair_pass": np.concatenate([scores[groups["holdout"]], scores[groups["pair_pass"]]]),
        "without_pair_pass": scores[groups["holdout"]],
    }
    return {
        "seed": seed,
        "sizes": {k: int(len(v)) for k, v in groups.items()},
        "threshold": threshold,
        "metrics": {name: compute_metrics(pos, neg, threshold) for name, neg in negatives.items()},
        # 쌍의 합격 행끼리만 비교한 오탐률 - 쌍의 합격 행이 일반 합격과 다르게 보이는지 확인용
        "pair_pass_fpr": float((scores[groups["pair_pass"]] > threshold).mean()) if len(groups["pair_pass"]) else None,
        "pair": pair_win_rate(scores, data["label"], data["pair_key"]),
    }


def summarize(results: list[dict]) -> dict:
    """시드별 결과 -> 지표별 평균·최소·최대"""
    def stat(values):
        values = [v for v in values if v is not None]
        if not values:
            return None
        return {"mean": float(np.mean(values)), "min": float(min(values)), "max": float(max(values))}

    out = {}
    for name in NEGATIVE_SETS:
        out[name] = {k: stat([r["metrics"][name][k] for r in results])
                     for k in ("precision", "recall", "f1", "fpr", "auc")}
    out["pair_pass_fpr"] = stat([r["pair_pass_fpr"] for r in results])
    out["pair_win_rate"] = stat([r["pair"]["rate"] for r in results])
    return out


def _fmt(s: dict | None) -> str:
    if s is None:
        return "-"
    return f"{s['mean']:.3f} ({s['min']:.3f}~{s['max']:.3f})"


def main() -> None:
    parser = argparse.ArgumentParser(description="정밀가공 품질 데이터 이상탐지 오프라인 평가")
    parser.add_argument("--holdout", type=float, default=0.3, help="일반 합격 행 중 평가용으로 떼어 둘 비율 (기본 0.3)")
    parser.add_argument("--quantile", type=float, default=0.99,
                        help="기준값 = 학습 정상 점수의 이 분위수 (기본 0.99, 워커 기본과 같음)")
    parser.add_argument("--seeds", type=int, default=5, help="분할·모델 시드 개수 (0..N-1)")
    parser.add_argument("--out", help="시드별 결과·요약 JSON 저장 경로 (evaluate/results/에 두면 gitignore)")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL", "postgresql://mes_user:mes_password@localhost:5432/mini_mes")
    data = load_records(database_url)
    n_pairs = len({k for k, c in zip(data["pair_key"], data["conflict"]) if c})
    print(f"데이터 {len(data['label'])}행 (불합격 {int(data['label'].sum())}, SerialNo 중복 쌍 {n_pairs}), "
          f"특징 {len(data['feature_names'])}개, holdout={args.holdout}, quantile={args.quantile}, 시드 {args.seeds}개")

    results = [evaluate_seed(data, args.holdout, args.quantile, seed) for seed in range(args.seeds)]
    sizes = results[0]["sizes"]
    print(f"분할: 학습 정상 {sizes['train']}, 평가 정상 {sizes['holdout']}, "
          f"쌍의 합격 {sizes['pair_pass']}, 이상(불합격) {sizes['fail']}")
    summary = summarize(results)

    print("\n지표: 평균 (최소~최대)")
    for name, desc in NEGATIVE_SETS.items():
        s = summary[name]
        print(f"[{name}] {desc}")
        print(f"  precision {_fmt(s['precision'])}  recall {_fmt(s['recall'])}  F1 {_fmt(s['f1'])}")
        print(f"  오탐률 {_fmt(s['fpr'])}  AUC {_fmt(s['auc'])}")
    print(f"쌍의 합격 행 오탐률 {_fmt(summary['pair_pass_fpr'])}")
    print(f"쌍 비교 (불합격 점수 > 같은 쌍 합격 점수) {_fmt(summary['pair_win_rate'])}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump({"args": vars(args), "summary": summary, "seeds": results}, f, ensure_ascii=False, indent=2)
        print(f"저장: {args.out}")


if __name__ == "__main__":
    main()
