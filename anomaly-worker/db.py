"""워커/학습 스크립트가 쓰는 DB 조회·기록 (psycopg2 직접 사용)"""
import os
from datetime import datetime
from typing import Optional

import psycopg2

DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://mes_user:mes_password@localhost:5432/mini_mes",
)


def connect():
    return psycopg2.connect(DATABASE_URL)


def list_equipment_ids(conn) -> list[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT equipment_id FROM equipment ORDER BY equipment_id")
        return [r[0] for r in cur.fetchall()]


def fetch_training_rows(conn, equipment_id: str, since: datetime, until: Optional[datetime]):
    """(ts, cycle_time_sec, qty_good, qty_defect) - 학습 구간의 생산실적"""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ts, cycle_time_sec, qty_good, qty_defect
            FROM production_log
            WHERE equipment_id = %s
              AND cycle_time_sec IS NOT NULL
              AND ts >= %s
              AND (%s::timestamptz IS NULL OR ts <= %s::timestamptz)
            ORDER BY ts
            """,
            (equipment_id, since, until, until),
        )
        return cur.fetchall()


def fetch_unscored_rows(conn, equipment_id: str, after: datetime, lookback_hours: int):
    """아직 anomaly_result에 기록되지 않은 최근 생산실적.

    anomaly_result에는 log_id 컬럼이 없으므로 (equipment_id, ts)로 같은 로그인지 판단한다.
    after(=학습 데이터의 마지막 ts) 이전 데이터는 학습에 쓰였으므로 추론 대상에서 제외한다.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.ts, p.cycle_time_sec, p.qty_good, p.qty_defect
            FROM production_log p
            WHERE p.equipment_id = %s
              AND p.cycle_time_sec IS NOT NULL
              AND p.ts > %s
              AND p.ts >= now() - make_interval(hours => %s)
              AND NOT EXISTS (
                  SELECT 1 FROM anomaly_result a
                  WHERE a.equipment_id = p.equipment_id AND a.ts = p.ts
              )
            ORDER BY p.ts
            """,
            (equipment_id, after, lookback_hours),
        )
        return cur.fetchall()


def fetch_context_rows(conn, equipment_id: str, before: datetime, limit: int):
    """before 직전의 생산실적 limit건 (시간순) - 이동 구간 특징 계산용 이력. 이미 판정된 건·학습 구간 건도 포함한다."""
    if limit <= 0:
        return []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT ts, cycle_time_sec, qty_good, qty_defect
            FROM production_log
            WHERE equipment_id = %s AND cycle_time_sec IS NOT NULL AND ts < %s
            ORDER BY ts DESC
            LIMIT %s
            """,
            (equipment_id, before, limit),
        )
        return list(reversed(cur.fetchall()))


def insert_results(conn, equipment_id: str, results) -> int:
    """results: (ts, anomaly_score, is_anomaly). 이미 있는 (equipment_id, ts)는 건너뛴다."""
    inserted = 0
    with conn.cursor() as cur:
        for ts, anomaly_score, is_anomaly in results:
            cur.execute(
                """
                INSERT INTO anomaly_result (equipment_id, ts, anomaly_score, is_anomaly)
                SELECT %s, %s, %s, %s
                WHERE NOT EXISTS (
                    SELECT 1 FROM anomaly_result WHERE equipment_id = %s AND ts = %s
                )
                """,
                (equipment_id, ts, round(float(anomaly_score), 4), bool(is_anomaly), equipment_id, ts),
            )
            inserted += cur.rowcount
    conn.commit()
    return inserted
