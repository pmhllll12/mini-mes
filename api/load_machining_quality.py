"""
정밀가공 품질보증 데이터셋(KAMP) CSV -> machining_quality_record 적재.

멱등: (serial_no, source_received_at, pass_or_fail) 기준 upsert라 다시 실행해도 행이 늘지 않는다 (값·received_at만 갱신).
--replay-start를 주면 원본 첫 행이 그 시각이 되도록 received_at 전체를 같은 간격만큼 이동한다 (원본 시각은 source_received_at에 그대로).
예) 라이브 데모: --replay-start now -> 원본 간격 그대로 앞으로 약 3일에 걸쳐 received_at이 현재 시각을 지나간다.

--if-empty면 테이블에 행이 있을 때 아무것도 하지 않는다 (Helm 설치·업그레이드 훅용 - 배포할 때마다 재생이 처음으로 돌아가지 않게).

api 이미지에 들어 있어 클러스터에서도 같은 스크립트를 쓴다 (차트 machiningData: 훅 Job + 재생 CronJob).
실행 (compose, api 이미지 사용 - 이미지를 다시 빌드한 뒤):
  docker compose run --rm -v ./data:/data:ro api \
      python load_machining_quality.py --csv /data/raw/precision_machining_quality_dataset.csv
"""
import argparse
import csv
import os
from datetime import datetime, timezone

from sqlalchemy import create_engine, text

import machining_quality as mq

DEFAULT_CSV = "data/raw/precision_machining_quality_dataset.csv"
DEFAULT_DATABASE_URL = "postgresql://mes_user:mes_password@localhost:5432/mini_mes"


def read_rows(path: str, encoding: str) -> list[dict]:
    with open(path, encoding=encoding, newline="") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames != mq.EXPECTED_HEADER:
            missing = [c for c in mq.EXPECTED_HEADER if c not in (reader.fieldnames or [])]
            extra = [c for c in (reader.fieldnames or []) if c not in mq.EXPECTED_HEADER]
            raise SystemExit(f"헤더가 예상과 다름 - 없는 열: {missing}, 추가 열: {extra}")
        return [mq.parse_row(r) for r in reader]


def parse_replay_start(value: str | None) -> datetime | None:
    if value is None:
        return None
    if value == "now":
        return datetime.now(timezone.utc)
    ts = datetime.fromisoformat(value)
    return ts if ts.tzinfo else ts.replace(tzinfo=mq.KST)


def assign_received_at(rows: list[dict], replay_start: datetime | None) -> None:
    shift = replay_start - min(r["source_received_at"] for r in rows) if replay_start else None
    for r in rows:
        r["received_at"] = r["source_received_at"] + shift if shift else r["source_received_at"]


def build_upsert_sql() -> str:
    columns = ["serial_no", "source_received_at", "received_at", "pass_or_fail", *mq.SENSOR_COLUMNS.values()]
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c not in ("serial_no", "source_received_at", "pass_or_fail"))
    return (
        f"INSERT INTO machining_quality_record ({', '.join(columns)}) "
        f"VALUES ({', '.join(':' + c for c in columns)}) "
        "ON CONFLICT (serial_no, source_received_at, pass_or_fail) "
        f"DO UPDATE SET {updates}, loaded_at = now() "
        "RETURNING (xmax = 0) AS inserted"  # 새로 들어간 행이면 xmax가 0
    )


# 같은 SerialNo가 둘 이상인 행 표시 (테이블 전체 기준, 바뀐 행만 갱신)
MARK_CONFLICTS_SQL = """
UPDATE machining_quality_record m
SET is_serial_conflict = c.conflict
FROM (
    SELECT record_id, count(*) OVER (PARTITION BY serial_no) > 1 AS conflict
    FROM machining_quality_record
) c
WHERE m.record_id = c.record_id AND m.is_serial_conflict IS DISTINCT FROM c.conflict
"""


def warn_if_not_constant(rows: list[dict]) -> None:
    changed = [c for c in mq.CONSTANT_COLUMNS if len({r[c] for r in rows}) > 1]
    if changed:
        print(f"[경고] 상수로 정의한 열에 서로 다른 값이 있음 - CONSTANT_COLUMNS 재검토 필요: {changed}")


def load(engine, rows: list[dict]) -> dict:
    sql = text(build_upsert_sql())
    with engine.begin() as conn:
        before = conn.execute(text("SELECT count(*) FROM machining_quality_record")).scalar_one()
        inserted = sum(1 for r in rows if conn.execute(sql, r).scalar_one())
        conflict_updates = conn.execute(text(MARK_CONFLICTS_SQL)).rowcount
        after = conn.execute(text("SELECT count(*) FROM machining_quality_record")).scalar_one()
        conflicts = conn.execute(
            text("SELECT count(*) FROM machining_quality_record WHERE is_serial_conflict")
        ).scalar_one()
    return {
        "before": before, "after": after, "inserted": inserted, "updated": len(rows) - inserted,
        "conflict_flag_changed": conflict_updates, "conflict_rows": conflicts,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="정밀가공 품질보증 데이터셋 적재")
    parser.add_argument("--csv", default=DEFAULT_CSV, help=f"원본 CSV 경로 (기본 {DEFAULT_CSV})")
    parser.add_argument("--encoding", default="utf-8-sig", help="CSV 인코딩 (기본 utf-8-sig, KAMP 원본은 UTF-8)")
    parser.add_argument(
        "--replay-start", default=None,
        help="received_at을 이동할 시작 시각 (now 또는 ISO, 시간대 없으면 KST). 생략하면 원본 시각 그대로",
    )
    parser.add_argument("--if-empty", action="store_true", help="테이블에 행이 이미 있으면 적재하지 않고 끝낸다")
    args = parser.parse_args()

    engine = create_engine(os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL), pool_pre_ping=True)
    if args.if_empty:
        with engine.connect() as conn:
            existing = conn.execute(text("SELECT count(*) FROM machining_quality_record")).scalar_one()
        if existing:
            print(f"--if-empty: 이미 {existing}행이 있어 적재하지 않음")
            return

    rows = read_rows(args.csv, args.encoding)
    if not rows:
        raise SystemExit("CSV에 데이터 행이 없음")
    warn_if_not_constant(rows)
    assign_received_at(rows, parse_replay_start(args.replay_start))

    src = sorted(r["source_received_at"] for r in rows)
    rec = sorted(r["received_at"] for r in rows)
    print(f"CSV {args.csv}: {len(rows)}행, 원본 {src[0].isoformat()} ~ {src[-1].isoformat()}")
    print(f"received_at {rec[0].astimezone(mq.KST).isoformat()} ~ {rec[-1].astimezone(mq.KST).isoformat()}")

    result = load(engine, rows)
    print(
        f"적재 전 {result['before']}행 -> 적재 후 {result['after']}행 "
        f"(신규 {result['inserted']}, 갱신 {result['updated']}), "
        f"SerialNo 중복 표시 {result['conflict_rows']}행 (이번에 바뀐 표시 {result['conflict_flag_changed']})"
    )


if __name__ == "__main__":
    main()
