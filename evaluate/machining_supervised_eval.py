"""
정밀가공 품질(KAMP) 합격/불합격 지도 학습 오프라인 평가 (DB에 기록하지 않음, 서비스·워커에는 넣지 않음).

비지도 이상 점수(machining_anomaly_eval.py)로는 불합격을 거의 못 잡아(AUC 약 0.59), 라벨로 분류기를 학습해 본다.
특징: 상수 열을 뺀 센서 27개 (machining_quality.FEATURE_COLUMNS). 모델: 로지스틱 회귀(표준화), 랜덤 포레스트.
둘 다 class_weight="balanced"이고 판정 기준은 확률 0.5로 고정한다 (평가 데이터로 기준값을 조정하지 않음).

검증 분할 - 같은 SerialNo·시각 쌍의 두 행은 항상 같은 쪽에 둔다 (쌍이 갈라지면 비슷한 조건의 행이 학습·평가에 섞임):
- cv:   쌍 단위 층화 K겹 교차검증 (StratifiedGroupKFold), 시드 여러 개로 반복
- time: 원본 수집 시각 앞쪽 --time-train 비율로 학습, 뒤쪽으로 평가 (나중 데이터 예측)

불합격 99건이 전부 SerialNo 중복 쌍에 속하므로, 모델이 "불량"이 아니라 "중복 기록된 행"을 구분하는지 확인한다:
- 쌍 비교: 같은 쌍에서 불합격 행 확률 > 합격 행 확률인 비율, 불합격 vs 쌍의 합격 행만으로 낸 AUC
- 쌍의 합격 행 오탐률 vs 일반 합격 오탐률 (중복 여부를 배웠다면 쌍의 합격 행 오탐률이 높아짐)
- 중복 판별 대조 실험: 합격 행만으로 "쌍의 합격 vs 일반 합격"을 같은 방법으로 학습 (AUC가 0.5 근처면 센서로 중복 여부를 알 수 없음)

실행 (anomaly-worker 이미지 - scikit-learn·psycopg2, api 코드는 열 정의용):
    docker compose run --rm -v "$PWD/evaluate:/eval" -v "$PWD/api:/api:ro" \\
        anomaly-worker python /eval/machining_supervised_eval.py
"""
import argparse
import json
import os
import warnings
from datetime import datetime, timedelta, timezone

import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, roc_auc_score
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from machining_anomaly_eval import load_records, pair_win_rate

THRESHOLD = 0.5
KST = timezone(timedelta(hours=9), "KST")
# scikit-learn 1.5.2 + 최신 SciPy 조합에서 로지스틱 회귀가 내는 무해한 경고 (결과와 무관)
warnings.filterwarnings("ignore", message="Unknown solver options")
MODELS = ("logistic", "random_forest")


def make_model(name: str, seed: int):
    if name == "logistic":
        return make_pipeline(StandardScaler(), LogisticRegression(class_weight="balanced", max_iter=2000))
    if name == "random_forest":
        return RandomForestClassifier(n_estimators=300, min_samples_leaf=2, class_weight="balanced",
                                      random_state=seed, n_jobs=-1)
    raise ValueError(name)


def group_ids(pair_key: list[str]) -> np.ndarray:
    """같은 SerialNo·시각이면 같은 그룹 번호"""
    index: dict[str, int] = {}
    return np.array([index.setdefault(k, len(index)) for k in pair_key])


def cv_predict(X, y, groups, model_name: str, folds: int, seed: int) -> np.ndarray:
    """쌍 단위 층화 K겹 -> 모든 행의 out-of-fold 불합격 확률"""
    prob = np.full(len(y), np.nan)
    splitter = StratifiedGroupKFold(n_splits=folds, shuffle=True, random_state=seed)
    for train, test in splitter.split(X, y, groups):
        model = make_model(model_name, seed).fit(X[train], y[train])
        prob[test] = model.predict_proba(X[test])[:, 1]
    return prob


def time_split(order_key: np.ndarray, groups: np.ndarray, train_frac: float) -> tuple[np.ndarray, np.ndarray]:
    """시각 순으로 앞쪽 train_frac(행 기준)을 학습, 나머지를 평가. 경계에 걸친 쌍은 평가 쪽으로 보낸다"""
    order = np.argsort(order_key, kind="stable")
    cut = int(round(len(order) * train_frac))
    test_groups = set(groups[order[cut:]])
    is_test = np.array([g in test_groups for g in groups])
    return np.flatnonzero(~is_test), np.flatnonzero(is_test)


def evaluate_predictions(prob: np.ndarray, y: np.ndarray, conflict: np.ndarray, pair_key: list[str],
                         idx: np.ndarray | None = None) -> dict:
    """확률 -> 지표. idx가 있으면 그 행만 평가 (time 분할의 평가 구간)"""
    if idx is None:
        idx = np.arange(len(y))
    p, t, c = prob[idx], y[idx], conflict[idx]
    pred = p >= THRESHOLD
    tp = int((pred & (t == 1)).sum())
    fp = int((pred & (t == 0)).sum())
    fn = int((~pred & (t == 1)).sum())
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    clean_pass, pair_pass, fail = (t == 0) & ~c, (t == 0) & c, t == 1
    pair_only = fail | pair_pass
    return {
        "n": int(len(idx)), "fail": int(fail.sum()),
        "auc": float(roc_auc_score(t, p)),
        "average_precision": float(average_precision_score(t, p)),
        "precision": precision, "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if precision + recall else 0.0,
        "clean_pass_fpr": float(pred[clean_pass].mean()) if clean_pass.any() else None,
        "pair_pass_fpr": float(pred[pair_pass].mean()) if pair_pass.any() else None,
        "pair_auc": float(roc_auc_score(t[pair_only], p[pair_only])) if pair_pass.any() else None,
        "pair": pair_win_rate(p, t, [pair_key[i] for i in idx]),
    }


def conflict_control(X, y, conflict, groups, model_name: str, folds: int, seed: int) -> float:
    """합격 행만으로 '쌍의 합격 vs 일반 합격'을 학습 -> out-of-fold AUC (0.5 근처면 센서로 중복 여부를 모름)"""
    passes = np.flatnonzero(y == 0)
    target = conflict[passes].astype(int)
    prob = cv_predict(X[passes], target, groups[passes], model_name, folds, seed)
    return float(roc_auc_score(target, prob))


def top_features(X, y, names: list[str], model_name: str, seed: int, k: int = 5) -> list[tuple[str, float]]:
    """전체 데이터로 학습한 모델의 중요 특징 (로지스틱: 표준화 계수, 포레스트: 불순도 감소). 해석용, 성능 수치 아님"""
    model = make_model(model_name, seed).fit(X, y)
    if model_name == "logistic":
        weights = model[-1].coef_[0]
    else:
        weights = model.feature_importances_
    order = np.argsort(-np.abs(weights))[:k]
    return [(names[i], float(weights[i])) for i in order]


def kst_range(times: np.ndarray) -> str:
    def fmt(value: str) -> str:
        return datetime.fromisoformat(value).astimezone(KST).strftime("%m-%d %H:%M")
    return f"{fmt(min(times))}~{fmt(max(times))} KST"


def stat(values: list) -> dict | None:
    values = [v for v in values if v is not None]
    if not values:
        return None
    return {"mean": float(np.mean(values)), "min": float(min(values)), "max": float(max(values))}


def summarize(runs: list[dict]) -> dict:
    keys = ("auc", "average_precision", "precision", "recall", "f1", "clean_pass_fpr", "pair_pass_fpr", "pair_auc")
    out = {k: stat([r[k] for r in runs]) for k in keys}
    out["pair_win_rate"] = stat([r["pair"]["rate"] for r in runs])
    return out


def _fmt(s: dict | None) -> str:
    if s is None:
        return "-"
    if s["min"] == s["max"]:
        return f"{s['mean']:.3f}"
    return f"{s['mean']:.3f} ({s['min']:.3f}~{s['max']:.3f})"


def print_summary(title: str, s: dict) -> None:
    print(title)
    print(f"  AUC {_fmt(s['auc'])}  AP {_fmt(s['average_precision'])}")
    print(f"  precision {_fmt(s['precision'])}  recall {_fmt(s['recall'])}  F1 {_fmt(s['f1'])}  (확률 {THRESHOLD} 기준)")
    print(f"  오탐률: 일반 합격 {_fmt(s['clean_pass_fpr'])} / 쌍의 합격 {_fmt(s['pair_pass_fpr'])}")
    print(f"  쌍 비교: 불합격 > 같은 쌍 합격 {_fmt(s['pair_win_rate'])}, 불합격 vs 쌍의 합격 AUC {_fmt(s['pair_auc'])}")


def main() -> None:
    parser = argparse.ArgumentParser(description="정밀가공 품질 합격/불합격 지도 학습 오프라인 평가")
    parser.add_argument("--folds", type=int, default=5, help="교차검증 겹 수 (기본 5)")
    parser.add_argument("--seeds", type=int, default=5, help="교차검증 분할·모델 시드 개수 (기본 5)")
    parser.add_argument("--time-train", type=float, default=0.67, help="time 분할에서 학습에 쓸 앞쪽 비율 (기본 0.67)")
    parser.add_argument("--out", help="결과 JSON 저장 경로 (evaluate/results/에 두면 gitignore)")
    args = parser.parse_args()

    database_url = os.getenv("DATABASE_URL", "postgresql://mes_user:mes_password@localhost:5432/mini_mes")
    data = load_records(database_url)
    X, y, conflict, keys = data["X"], data["label"], data["conflict"], data["pair_key"]
    groups = group_ids(keys)
    times = np.array([k.split("|", 1)[1] for k in keys])   # ISO 문자열이라 사전순 = 시각순
    print(f"데이터 {len(y)}행 (불합격 {int(y.sum())}, SerialNo 중복 쌍 {int(conflict.sum()) // 2}), "
          f"특징 {X.shape[1]}개, {args.folds}겹 × 시드 {args.seeds}개, time 학습 비율 {args.time_train}")

    result = {"args": vars(args)}
    train_idx, test_idx = time_split(times, groups, args.time_train)
    print(f"time 분할: 학습 {len(train_idx)}행(불합격 {int(y[train_idx].sum())}) "
          f"{kst_range(times[train_idx])}, "
          f"평가 {len(test_idx)}행(불합격 {int(y[test_idx].sum())}) {kst_range(times[test_idx])}")

    for name in MODELS:
        cv_runs = [evaluate_predictions(cv_predict(X, y, groups, name, args.folds, seed), y, conflict, keys)
                   for seed in range(args.seeds)]
        time_runs = []
        for seed in range(args.seeds):
            prob = np.full(len(y), np.nan)
            model = make_model(name, seed).fit(X[train_idx], y[train_idx])
            prob[test_idx] = model.predict_proba(X[test_idx])[:, 1]
            time_runs.append(evaluate_predictions(prob, y, conflict, keys, test_idx))
        control = [conflict_control(X, y, conflict, groups, name, args.folds, seed) for seed in range(args.seeds)]
        features = top_features(X, y, data["feature_names"], name, 0)

        cv_s, time_s = summarize(cv_runs), summarize(time_runs)
        print(f"\n===== {name} =====")
        print_summary("[cv] 쌍 단위 층화 교차검증 - 평균 (최소~최대)", cv_s)
        print_summary("[time] 앞쪽 학습 → 뒤쪽 평가", time_s)
        print(f"중복 판별 대조 실험(합격 행만, 쌍의 합격 vs 일반 합격) AUC {_fmt(stat(control))}")
        print("중요 특징(전체 데이터 학습, 해석용): " + ", ".join(f"{n} {w:+.3f}" for n, w in features))
        result[name] = {"cv": cv_s, "time": time_s, "conflict_control_auc": stat(control),
                        "top_features": features, "cv_runs": cv_runs, "time_runs": time_runs}

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n저장: {args.out}")


if __name__ == "__main__":
    main()
