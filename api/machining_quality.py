"""
정밀가공 품질보증 데이터셋(KAMP) - 원본 CSV 컬럼과 DB 컬럼의 대응, 조회에서 뺄 열 정의.

- 한 행 = 가공 1건의 센서 요약(max/mean/min/std) + 합격/불합격 판정
- 센서 값은 데이터셋 가이드북에 단위 정의가 없어 설비 원시값 그대로 저장하고, 컬럼명에 _raw를 붙인다 (단위 미상)
- ReceivedDateTime은 시간대 표기가 없어 KST로 간주한다 (가정 - README "사용 데이터" 참고)
- passorfail: 1=불합격(불량), 0=합격
"""
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9), "KST")

# 원본 식별·판정 컬럼 -> DB 컬럼
SERIAL_COLUMN = "SerialNo"           # -> serial_no (같은 SerialNo가 0/1로 두 번 나오는 경우가 있어 PK로 쓰지 않음)
RECEIVED_COLUMN = "ReceivedDateTime"  # -> source_received_at (원본 그대로 보존, received_at은 재생 시 이동한 시각)
LABEL_COLUMN = "passorfail"          # -> pass_or_fail

# 원본 센서 컬럼 -> DB 컬럼 (원본 순서 그대로)
SENSOR_COLUMNS = {
    "SpindleSpeed_max": "spindle_speed_max_raw",
    "ServoCurrent_X_max": "servo_current_x_max_raw",
    "ServoCurrent_Z1_max": "servo_current_z1_max_raw",
    "ServoCurrent_Z2_max": "servo_current_z2_max_raw",
    "ServoCurrent_Z3_max": "servo_current_z3_max_raw",
    "ServoLoad_X_max": "servo_load_x_max_raw",
    "ServoLoad_Z1_max": "servo_load_z1_max_raw",
    "ServoLoad_Z2_max": "servo_load_z2_max_raw",
    "ServoLoad_Z3_max": "servo_load_z3_max_raw",
    "SpindleSpeed_mean": "spindle_speed_mean_raw",
    "ServoCurrent_X_mean": "servo_current_x_mean_raw",
    "ServoCurrent_Z1_mean": "servo_current_z1_mean_raw",
    "ServoCurrent_Z2_mean": "servo_current_z2_mean_raw",
    "ServoCurrent_Z3_mean": "servo_current_z3_mean_raw",
    "ServoLoad_X_mean": "servo_load_x_mean_raw",
    "ServoLoad_Z1_mean": "servo_load_z1_mean_raw",
    "ServoLoad_Z2_mean": "servo_load_z2_mean_raw",
    "ServoLoad_Z3_mean": "servo_load_z3_mean_raw",
    "SpindleSpeed_min": "spindle_speed_min_raw",
    "ServoCurrent_X_min": "servo_current_x_min_raw",
    "ServoCurrent_Z1_min": "servo_current_z1_min_raw",
    "ServoCurrent_Z2_min": "servo_current_z2_min_raw",
    "ServoCurrent_Z3_min": "servo_current_z3_min_raw",
    "ServoLoad_X_min": "servo_load_x_min_raw",
    "ServoLoad_Z1_min": "servo_load_z1_min_raw",
    "ServoLoad_Z2_min": "servo_load_z2_min_raw",
    "ServoLoad_Z3_min": "servo_load_z3_min_raw",
    "SpindleSpeed_std": "spindle_speed_std_raw",
    "ServoCurrent_X_std": "servo_current_x_std_raw",
    "ServoCurrent_Z1_std": "servo_current_z1_std_raw",
    "ServoCurrent_Z2_std": "servo_current_z2_std_raw",
    "ServoCurrent_Z3_std": "servo_current_z3_std_raw",
    "ServoLoad_X_std": "servo_load_x_std_raw",
    "ServoLoad_Z1_std": "servo_load_z1_std_raw",
    "ServoLoad_Z2_std": "servo_load_z2_std_raw",
    "ServoLoad_Z3_std": "servo_load_z3_std_raw",
    "SpindleLoad_max": "spindle_load_max_raw",
    "SpindleLoad_mean": "spindle_load_mean_raw",
    "SpindleLoad_min": "spindle_load_min_raw",
    "SpindleLoad_std": "spindle_load_std_raw",
}

# 원본 1,085행 전부 같은 값인 열 (2026-10-13 확인). 테이블에는 저장하지만 이상탐지 피처·대시보드 조회에서는 뺀다
# Z2축 8개는 전부 30.3(std는 0) - 미사용 축으로 보이나 가이드북에 설명 없음
CONSTANT_COLUMNS = (
    "servo_current_z2_max_raw", "servo_current_z2_mean_raw", "servo_current_z2_min_raw", "servo_current_z2_std_raw",
    "servo_load_z2_max_raw", "servo_load_z2_mean_raw", "servo_load_z2_min_raw", "servo_load_z2_std_raw",
    "spindle_speed_min_raw", "spindle_load_min_raw", "servo_load_x_min_raw",
    "servo_current_z3_min_raw", "servo_load_z3_min_raw",
)

# 조회·피처에 쓰는 센서 열 (상수 열 제외, 원본 순서)
FEATURE_COLUMNS = tuple(c for c in SENSOR_COLUMNS.values() if c not in CONSTANT_COLUMNS)

EXPECTED_HEADER = [SERIAL_COLUMN, RECEIVED_COLUMN, *SENSOR_COLUMNS, LABEL_COLUMN]


def parse_received(value: str) -> datetime:
    """'2022-08-23 03:14:36.906' (시간대 없음) -> KST aware datetime"""
    return datetime.fromisoformat(value.strip()).replace(tzinfo=KST)


def parse_row(row: dict) -> dict:
    """원본 CSV 1행(DictReader) -> DB 컬럼 dict. received_at은 적재 시 정하므로 여기서는 넣지 않는다"""
    label = int(row[LABEL_COLUMN])
    if label not in (0, 1):
        raise ValueError(f"passorfail 값이 0/1이 아님: {row[LABEL_COLUMN]!r} (SerialNo={row[SERIAL_COLUMN]})")
    out = {
        "serial_no": row[SERIAL_COLUMN].strip(),
        "source_received_at": parse_received(row[RECEIVED_COLUMN]),
        "pass_or_fail": label,
    }
    for src, dst in SENSOR_COLUMNS.items():
        out[dst] = float(row[src])
    return out
