import json
from datetime import datetime, timezone

import pytest

from evaluate import compute_metrics, load_labels, summarize_runs


def test_compute_metrics_counts_and_scores():
    pairs = [(True, True)] * 3 + [(False, True)] + [(True, False)] * 2 + [(False, False)] * 4
    m = compute_metrics(pairs)
    assert (m["tp"], m["fp"], m["fn"], m["tn"], m["n"]) == (3, 1, 2, 4, 10)
    assert m["precision"] == pytest.approx(0.75)
    assert m["recall"] == pytest.approx(0.6)
    assert m["f1"] == pytest.approx(2 * 0.75 * 0.6 / 1.35)


def test_compute_metrics_no_predictions_does_not_divide_by_zero():
    m = compute_metrics([(True, False), (False, False)])
    assert m["precision"] == 0.0 and m["recall"] == 0.0 and m["f1"] == 0.0


def test_load_labels_and_summarize_runs(tmp_path):
    path = tmp_path / "labels.jsonl"
    recs = [
        {"run_id": "A", "anomaly_rate": 0.0, "equipment_id": "EQ-001",
         "ts": "2026-09-28T03:00:00.000001Z", "log_id": 1, "is_anomaly": False},
        {"run_id": "B", "anomaly_rate": 0.2, "equipment_id": "EQ-001",
         "ts": "2026-09-28T04:00:00Z", "log_id": 2, "is_anomaly": True},
        {"run_id": "B", "anomaly_rate": 0.2, "equipment_id": "EQ-002",
         "ts": "2026-09-28T04:00:05Z", "log_id": 3, "is_anomaly": False},
    ]
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")

    labels = load_labels(str(path))
    # API 응답의 ts(Z 표기)를 DB 값과 비교할 수 있는 aware datetime으로 읽는다
    assert labels[0]["ts"] == datetime(2026, 9, 28, 3, 0, 0, 1, tzinfo=timezone.utc)

    runs = summarize_runs(labels)
    assert [(r["run_id"], r["count"], r["anomalies"]) for r in runs] == [("A", 1, 0), ("B", 2, 1)]
    assert runs[1]["start"] < runs[1]["end"]


def test_recall_by_type_counts_only_scored_anomalies():
    from evaluate import recall_by_type

    ts = [datetime(2026, 9, 28, 4, 0, i, tzinfo=timezone.utc) for i in range(5)]
    labels = [
        {"equipment_id": "EQ-001", "ts": ts[0], "is_anomaly": True, "anomaly_type": "drift"},
        {"equipment_id": "EQ-001", "ts": ts[1], "is_anomaly": True, "anomaly_type": "drift"},
        {"equipment_id": "EQ-001", "ts": ts[2], "is_anomaly": True},            # 예전 라벨 -> spike
        {"equipment_id": "EQ-001", "ts": ts[3], "is_anomaly": False, "anomaly_type": None},
        {"equipment_id": "EQ-001", "ts": ts[4], "is_anomaly": True, "anomaly_type": "spike"},  # 미판정
    ]
    preds = {("EQ-001", ts[0]): True, ("EQ-001", ts[1]): False, ("EQ-001", ts[2]): True, ("EQ-001", ts[3]): True}
    assert recall_by_type(labels, preds) == {"drift": (1, 2), "spike": (1, 1)}
