"""
정밀가공 품질(KAMP) 합격/불합격 지도 학습 -> 학습 기간 이후 행 배치 예측 -> machining_prediction 저장.

설비 이상탐지(비지도, 정상 데이터만 학습)와 별도 도메인이다. 이 데이터는 합격/불합격 라벨이 있고 비지도로는
불합격을 거의 못 잡아서(evaluate/machining_anomaly_eval.py, AUC 약 0.59) 라벨로 분류기를 학습한다.

- 분할: 원본 수집 시각(source_received_at) 기준. --train-until 이전 행으로 학습하고, 이후 행만 예측한다
  (학습한 행은 예측하지 않는다 - 같은 행을 예측하면 성능이 아니라 정답을 본 결과). SerialNo 중복 쌍은 시각이
  같아 항상 같은 쪽에 들어간다. 재생 적재의 "도착" 여부와는 무관하며, API는 도착한 행의 예측만 보여준다
- 모델: 로지스틱 회귀(표준화, class_weight="balanced"), 판정 기준 확률 0.5 고정. 오프라인 평가
  (evaluate/machining_supervised_eval.py)의 두 모델 중 더 단순한 쪽을 미리 골랐다 (예측 결과를 보고 고르지 않음)
- 실시간 예측이 아니라 실행할 때 한 번에 예측하는 배치다. 다시 실행하면 이전 예측을 지우고 새로 쓴다
- 해석 주의: 스핀들 부하 규칙 하나로도 F1 0.92라 라벨이 그 신호로 매겨졌을 가능성, 설비 1대·3일 데이터

실행 (워커 이미지, DATABASE_URL 필요):
    docker compose run --rm anomaly-worker python machining_predict.py [--train-until 2022-08-25] [--dry-run]
    kubectl exec deploy/<release>-anomaly-worker -- python machining_predict.py
"""
import argparse
import os
import warnings
from datetime import datetime, timedelta, timezone

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

KST = timezone(timedelta(hours=9), "KST")
MODEL_NAME = "logistic_regression"
THRESHOLD = 0.5
# scikit-learn 1.5.2 + 최신 SciPy 조합에서 로지스틱 회귀가 내는 무해한 경고 (결과와 무관)
warnings.filterwarnings("ignore", message="Unknown solver options")

# api/machining_quality.py의 FEATURE_COLUMNS(상수 열을 뺀 센서 27개)와 같아야 한다 - 워커 이미지에는 api 코드가
# 없어 복사해 둔다 (tests/test_machining_predict.py가 저장소의 api 파일과 비교)
FEATURE_COLUMNS = (
    "spindle_speed_max_raw", "servo_current_x_max_raw", "servo_current_z1_max_raw", "servo_current_z3_max_raw",
    "servo_load_x_max_raw", "servo_load_z1_max_raw", "servo_load_z3_max_raw",
    "spindle_speed_mean_raw", "servo_current_x_mean_raw", "servo_current_z1_mean_raw", "servo_current_z3_mean_raw",
    "servo_load_x_mean_raw", "servo_load_z1_mean_raw", "servo_load_z3_mean_raw",
    "servo_current_x_min_raw", "servo_current_z1_min_raw", "servo_load_z1_min_raw",
    "spindle_speed_std_raw", "servo_current_x_std_raw", "servo_current_z1_std_raw", "servo_current_z3_std_raw",
    "servo_load_x_std_raw", "servo_load_z1_std_raw", "servo_load_z3_std_raw",
    "spindle_load_max_raw", "spindle_load_mean_raw", "spindle_load_std_raw",
)

# db/schema.sql과 같은 정의. 이미 운영 중인 DB(schema.sql이 다시 실행되지 않음)에서도 이 CLI만으로 동작하게 한다
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS machining_prediction (
    record_id        BIGINT PRIMARY KEY REFERENCES machining_quality_record (record_id) ON DELETE CASCADE,
    fail_probability DOUBLE PRECISION NOT NULL CHECK (fail_probability BETWEEN 0 AND 1),
    predicted_fail   SMALLINT NOT NULL CHECK (predicted_fail IN (0, 1)),
    model_name       VARCHAR(40) NOT NULL,
    threshold        DOUBLE PRECISION NOT NULL,
    trained_until    TIMESTAMPTZ NOT NULL,
    train_rows       INTEGER NOT NULL,
    train_fail       INTEGER NOT NULL,
    predicted_at     TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


def parse_cutoff(value: str) -> datetime:
    """'2022-08-25' 또는 ISO 시각 (시간대 없으면 KST) -> aware datetime"""
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=KST)


def split_by_cutoff(source_times: list[datetime], cutoff: datetime) -> tuple[np.ndarray, np.ndarray]:
    """(학습 행 번호, 예측 행 번호). 원본 시각이 cutoff보다 이르면 학습, 같거나 늦으면 예측"""
    is_train = np.array([t < cutoff for t in source_times], dtype=bool)
    return np.flatnonzero(is_train), np.flatnonzero(~is_train)


def make_model():
    return make_pipeline(StandardScaler(), LogisticRegression(class_weight="balanced", max_iter=2000))


def fit_predict(X_train: np.ndarray, y_train: np.ndarray, X_pred: np.ndarray) -> np.ndarray:
    """학습 행으로 학습 -> 예측 행의 불합격 확률"""
    if len(set(y_train.tolist())) < 2:
        raise SystemExit("학습 구간에 합격·불합격이 모두 있어야 함 - --train-until을 확인")
    return make_model().fit(X_train, y_train).predict_proba(X_pred)[:, 1]


def confusion(actual: np.ndarray, predicted: np.ndarray) -> dict:
    return {
        "tp": int(((predicted == 1) & (actual == 1)).sum()),
        "fp": int(((predicted == 1) & (actual == 0)).sum()),
        "fn": int(((predicted == 0) & (actual == 1)).sum()),
        "tn": int(((predicted == 0) & (actual == 0)).sum()),
    }


def load_rows(cur) -> list[tuple]:
    cur.execute(
        f"SELECT record_id, source_received_at, pass_or_fail, {', '.join(FEATURE_COLUMNS)} "
        "FROM machining_quality_record ORDER BY source_received_at, record_id"
    )
    return cur.fetchall()


def save_predictions(cur, rows: list[tuple]) -> None:
    """이전 예측을 지우고 새로 쓴다 (같은 트랜잭션 - 중간 실패 시 이전 예측 유지)"""
    from psycopg2.extras import execute_values

    cur.execute(CREATE_TABLE_SQL)
    cur.execute("DELETE FROM machining_prediction")
    execute_values(
        cur,
        "INSERT INTO machining_prediction (record_id, fail_probability, predicted_fail, model_name, threshold, "
        "trained_until, train_rows, train_fail) VALUES %s",
        rows,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="정밀가공 합격/불합격 학습 + 학습 기간 이후 행 배치 예측")
    parser.add_argument("--train-until", default="2022-08-25",
                        help="이 원본 시각 이전 행으로 학습, 이후 행을 예측 (기본 2022-08-25 = 08-23~24 학습, 08-25 예측, KST)")
    parser.add_argument("--dry-run", action="store_true", help="DB에 쓰지 않고 결과만 출력")
    args = parser.parse_args()

    import psycopg2

    cutoff = parse_cutoff(args.train_until)
    database_url = os.getenv("DATABASE_URL", "postgresql://mes_user:mes_password@localhost:5432/mini_mes")
    with psycopg2.connect(database_url) as conn, conn.cursor() as cur:
        rows = load_rows(cur)
        if not rows:
            raise SystemExit("machining_quality_record가 비어 있음 - 먼저 적재 (api/load_machining_quality.py)")
        record_ids = [r[0] for r in rows]
        y = np.array([r[2] for r in rows], dtype=int)
        X = np.array([r[3:] for r in rows], dtype=float)
        train_idx, pred_idx = split_by_cutoff([r[1] for r in rows], cutoff)
        if len(pred_idx) == 0:
            raise SystemExit(f"{cutoff.isoformat()} 이후 행이 없어 예측할 대상이 없음")

        prob = fit_predict(X[train_idx], y[train_idx], X[pred_idx])
        predicted = (prob >= THRESHOLD).astype(int)
        train_fail = int(y[train_idx].sum())
        c = confusion(y[pred_idx], predicted)
        print(f"학습: 원본 {cutoff.astimezone(KST).isoformat()} 이전 {len(train_idx)}행(불합격 {train_fail}), "
              f"예측: 이후 {len(pred_idx)}행(실제 불합격 {int(y[pred_idx].sum())})")
        print(f"모델 {MODEL_NAME}, 확률 {THRESHOLD} 기준 - 예측 불합격 {int(predicted.sum())}, "
              f"TP {c['tp']} FP {c['fp']} FN {c['fn']} TN {c['tn']}")

        if args.dry_run:
            print("--dry-run: DB에 쓰지 않음")
            return
        save_predictions(cur, [
            (record_ids[i], float(p), int(d), MODEL_NAME, THRESHOLD, cutoff, len(train_idx), train_fail)
            for i, p, d in zip(pred_idx, prob, predicted)
        ])
    print(f"machining_prediction에 {len(pred_idx)}행 저장")


if __name__ == "__main__":
    main()
