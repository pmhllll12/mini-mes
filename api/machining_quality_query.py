"""
정밀가공 품질 기록(machining_quality_record) 조회·집계

- 조회 기준 시각은 received_at. 재생 적재(--replay-start)면 미래 시각 행이 미리 들어 있으므로
  항상 received_at <= 지금 인 행만 보여준다 (end를 지금으로 자른다)
- 센서 비교는 상수 열을 뺀 FEATURE_COLUMNS만 쓴다
- SerialNo 중복 행(is_serial_conflict)은 원본에서 같은 시각에 0/1이 한 쌍으로 붙은 경우라 라벨 노이즈일 수 있어
  include_serial_conflict=False로 뺄 수 있게 한다 (현재 원본은 불합격 99건이 전부 이 쌍에 속함)
"""
import math
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from machining_quality import CONSTANT_COLUMNS, FEATURE_COLUMNS, KST, SENSOR_COLUMNS
from models import MachiningQualityRecord as R

BUCKETS = ("hour", "day")


def resolve_range(start: Optional[datetime], end: Optional[datetime]) -> tuple[Optional[datetime], datetime]:
    """end는 지금을 넘지 않게 자른다 (재생 중 아직 '도착'하지 않은 행 숨김). start가 없으면 처음부터"""
    now = datetime.now(timezone.utc)
    end = min(end, now) if end else now
    return start, end


def _filters(start: Optional[datetime], end: datetime, include_serial_conflict: bool, pass_or_fail: Optional[int] = None):
    conds = [R.received_at <= end]
    if start:
        conds.append(R.received_at >= start)
    if not include_serial_conflict:
        conds.append(R.is_serial_conflict.is_(False))
    if pass_or_fail is not None:
        conds.append(R.pass_or_fail == pass_or_fail)
    return conds


def record_to_dict(r, include_constant: bool) -> dict:
    sensors = SENSOR_COLUMNS.values() if include_constant else FEATURE_COLUMNS
    return {
        "record_id": r.record_id,
        "serial_no": r.serial_no,
        "received_at": r.received_at,
        "source_received_at": r.source_received_at,
        "pass_or_fail": r.pass_or_fail,
        "is_serial_conflict": r.is_serial_conflict,
        "sensors": {c: getattr(r, c) for c in sensors},
    }


def list_records(
    db: Session,
    start: Optional[datetime],
    end: datetime,
    pass_or_fail: Optional[int],
    include_serial_conflict: bool,
    limit: Optional[int],
) -> tuple[int, list]:
    """(조건에 맞는 전체 건수, received_at 순 행 목록 - limit이 있으면 앞에서부터 limit건)"""
    conds = _filters(start, end, include_serial_conflict, pass_or_fail)
    total = db.query(func.count(R.record_id)).filter(*conds).scalar()
    query = db.query(R).filter(*conds).order_by(R.received_at, R.record_id)
    if limit is not None:
        query = query.limit(limit)
    return int(total), query.all()


def _effect_size(n_pass, mean_pass, sd_pass, n_fail, mean_fail, sd_fail) -> Optional[float]:
    """불합격 평균 - 합격 평균을 합동 표준편차로 나눈 값 (Cohen's d). 계산할 수 없으면 None"""
    if n_pass < 2 or n_fail < 2 or sd_pass is None or sd_fail is None:
        return None
    pooled = math.sqrt(((n_pass - 1) * sd_pass**2 + (n_fail - 1) * sd_fail**2) / (n_pass + n_fail - 2))
    if pooled == 0:
        return None
    return (mean_fail - mean_pass) / pooled


def summarize(
    db: Session,
    start: Optional[datetime],
    end: datetime,
    bucket: str,
    include_serial_conflict: bool,
) -> dict:
    if bucket not in BUCKETS:
        raise ValueError(f"bucket은 {BUCKETS} 중 하나")
    conds = _filters(start, end, include_serial_conflict)

    # 기간별 판정 집계 (KST 기준으로 자른 뒤 KST 시각으로 돌려준다)
    local_bucket = func.date_trunc(bucket, func.timezone("Asia/Seoul", R.received_at)).label("bucket")
    bucket_rows = (
        db.query(
            local_bucket,
            func.count(R.record_id).label("total"),
            func.count(R.record_id).filter(R.pass_or_fail == 1).label("fail"),
        )
        .filter(*conds)
        .group_by(local_bucket)
        .order_by(local_bucket)
        .all()
    )
    buckets = [
        {
            "bucket_start": b.bucket.replace(tzinfo=KST),
            "total": int(b.total),
            "fail": int(b.fail),
            "fail_rate": round(b.fail / b.total, 4) if b.total else None,
        }
        for b in bucket_rows
    ]
    total = sum(b["total"] for b in buckets)
    fail = sum(b["fail"] for b in buckets)

    # 합격/불합격 그룹별 센서 평균·표준편차 (상수 열 제외)
    stat_columns = [R.pass_or_fail, func.count(R.record_id).label("n")]
    for c in FEATURE_COLUMNS:
        stat_columns += [func.avg(getattr(R, c)).label(f"{c}__mean"), func.stddev_samp(getattr(R, c)).label(f"{c}__sd")]
    groups = {row.pass_or_fail: row._mapping for row in db.query(*stat_columns).filter(*conds).group_by(R.pass_or_fail)}
    ok, ng = groups.get(0), groups.get(1)

    comparison = []
    for c in FEATURE_COLUMNS:
        pass_mean = float(ok[f"{c}__mean"]) if ok else None
        fail_mean = float(ng[f"{c}__mean"]) if ng else None
        d = None
        if ok and ng:
            d = _effect_size(
                ok["n"], pass_mean, ok[f"{c}__sd"] and float(ok[f"{c}__sd"]),
                ng["n"], fail_mean, ng[f"{c}__sd"] and float(ng[f"{c}__sd"]),
            )
        comparison.append({
            "column": c,
            "pass_mean": pass_mean,
            "fail_mean": fail_mean,
            "effect_size": round(d, 3) if d is not None else None,
        })
    # 차이가 큰 센서부터 (계산 못 한 열은 뒤로)
    comparison.sort(key=lambda x: (x["effect_size"] is None, -abs(x["effect_size"] or 0)))

    return {
        "start": start,
        "end": end,
        "bucket": bucket,
        "include_serial_conflict": include_serial_conflict,
        "total": total,
        "fail": fail,
        "fail_rate": round(fail / total, 4) if total else None,
        "buckets": buckets,
        "sensor_comparison": comparison,
        "excluded_constant_columns": list(CONSTANT_COLUMNS),
    }


def csv_columns(include_constant: bool) -> List[str]:
    sensors = list(SENSOR_COLUMNS.values()) if include_constant else list(FEATURE_COLUMNS)
    return ["record_id", "serial_no", "received_at", "source_received_at", "pass_or_fail", "is_serial_conflict", *sensors]
