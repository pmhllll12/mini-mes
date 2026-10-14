-- mini-mes: 핵심 3테이블 + 이상탐지 결과 테이블

CREATE TABLE IF NOT EXISTS equipment (
    equipment_id    VARCHAR(20) PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,
    line_id         VARCHAR(20) NOT NULL,
    status          VARCHAR(20) NOT NULL DEFAULT 'idle', -- 가동 / 정지 / 점검 / idle
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS production_log (
    log_id          BIGSERIAL PRIMARY KEY,
    equipment_id    VARCHAR(20) NOT NULL REFERENCES equipment(equipment_id),
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    qty_good        INTEGER NOT NULL DEFAULT 0,
    qty_defect      INTEGER NOT NULL DEFAULT 0,
    cycle_time_sec  NUMERIC(10,2),           -- 1개 생산에 걸린 시간(초)
    planned_time_sec NUMERIC(10,2)           -- 계획된 가동 시간(초) - 가동률 계산용
);

CREATE INDEX IF NOT EXISTS idx_production_log_equipment_ts
    ON production_log (equipment_id, ts DESC);

CREATE TABLE IF NOT EXISTS quality_event (
    event_id            BIGSERIAL PRIMARY KEY,
    equipment_id        VARCHAR(20) NOT NULL REFERENCES equipment(equipment_id),
    production_log_id   BIGINT REFERENCES production_log(log_id), -- 어떤 생산실적 건에서 발생한 불량인지 (nullable: 생산실적과 무관한 이벤트도 있을 수 있음)
    ts                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    defect_type         VARCHAR(50) NOT NULL,
    severity            VARCHAR(10) NOT NULL DEFAULT 'low', -- low / medium / high
    note                TEXT
);

CREATE INDEX IF NOT EXISTS idx_quality_event_equipment_ts
    ON quality_event (equipment_id, ts DESC);

CREATE INDEX IF NOT EXISTS idx_quality_event_production_log_id
    ON quality_event (production_log_id);

-- 5주차(이상탐지)에서 사용할 결과 테이블 - 미리 만들어둠
CREATE TABLE IF NOT EXISTS anomaly_result (
    result_id       BIGSERIAL PRIMARY KEY,
    equipment_id    VARCHAR(20) NOT NULL REFERENCES equipment(equipment_id),
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    anomaly_score   NUMERIC(10,4) NOT NULL,
    is_anomaly      BOOLEAN NOT NULL DEFAULT false
);

CREATE INDEX IF NOT EXISTS idx_anomaly_result_equipment_ts
    ON anomaly_result (equipment_id, ts DESC);

-- 열화 경보 이력 (anomaly-worker가 기록). 경보 1건 = 1행: 시작 시 추가, 해제 시 cleared_ts를 채운다.
-- raised_ts/cleared_ts = 경보를 켜고/끈 판정 대상 생산실적의 ts. cleared_ts가 NULL이면 경보 중
CREATE TABLE IF NOT EXISTS drift_alarm (
    alarm_id        BIGSERIAL PRIMARY KEY,
    equipment_id    VARCHAR(20) NOT NULL REFERENCES equipment(equipment_id),
    raised_ts       TIMESTAMPTZ NOT NULL,
    raised_score    NUMERIC(10,4) NOT NULL,
    threshold       NUMERIC(10,4) NOT NULL,
    cleared_ts      TIMESTAMPTZ,
    UNIQUE (equipment_id, raised_ts)
);
-- 설비당 진행 중인 경보는 하나만
CREATE UNIQUE INDEX IF NOT EXISTS uq_drift_alarm_open
    ON drift_alarm (equipment_id) WHERE cleared_ts IS NULL;

-- 초기 설비 3대 시드 데이터
INSERT INTO equipment (equipment_id, name, line_id, status) VALUES
    ('EQ-001', 'CNC 가공기 1호', 'LINE-A', 'idle'),
    ('EQ-002', 'CNC 가공기 2호', 'LINE-A', 'idle'),
    ('EQ-003', '포장 설비 1호', 'LINE-B', 'idle')
ON CONFLICT (equipment_id) DO NOTHING;

-- 일일 리포트 스냅샷 (POST /reports/daily, Helm CronJob이 매일 전날분 생성). KST 하루 기준, 같은 날짜를 다시 만들면 덮어씀
CREATE TABLE IF NOT EXISTS daily_report (
    report_date       DATE NOT NULL,
    equipment_id      VARCHAR(20) NOT NULL REFERENCES equipment(equipment_id),
    availability      NUMERIC(6,4) NOT NULL,
    quality_rate      NUMERIC(6,4) NOT NULL,
    oee               NUMERIC(6,4) NOT NULL,
    total_qty         INTEGER NOT NULL,
    total_defect      INTEGER NOT NULL,
    top_defect_type   VARCHAR(50),              -- 이벤트 건수 최다 불량 유형 (없으면 NULL)
    top_defect_events INTEGER NOT NULL,         -- 그 유형의 품질 이벤트 건수
    top_defect_qty    INTEGER NOT NULL,         -- 그 유형 이벤트에 연결된 불량 수량(개)
    anomaly_scored    INTEGER NOT NULL,         -- 급변 판정 건수
    anomaly_count     INTEGER NOT NULL,         -- 그중 이상
    drift_alarms      INTEGER NOT NULL,         -- 이날 시작한 열화 경보 수
    drift_alarm_sec   NUMERIC(12,1) NOT NULL,   -- 이날 열화 경보가 켜져 있던 시간 합(초)
    generated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (report_date, equipment_id)
);

-- 정밀가공 품질보증 데이터셋(KAMP, 2022-08-23~25 가공 1,085건). 기존 설비 테이블과 연결하지 않는 별도 도메인
-- 적재: api/load_machining_quality.py (원본 CSV는 커밋하지 않음 - README "사용 데이터")
-- 센서 값은 단위 미상(설비 원시값)이라 _raw. 원본 컬럼명 대응은 api/machining_quality.py
CREATE TABLE IF NOT EXISTS machining_quality_record (
    record_id          BIGSERIAL PRIMARY KEY,      -- SerialNo가 0/1로 두 번 나오는 경우가 있어 대리 키 사용
    serial_no          VARCHAR(30) NOT NULL,
    source_received_at TIMESTAMPTZ NOT NULL,       -- 원본 ReceivedDateTime (KST로 간주)
    received_at        TIMESTAMPTZ NOT NULL,       -- 조회 기준 시각. 기본은 원본과 같고, 재생(--replay-start) 적재면 이동한 시각
    pass_or_fail       SMALLINT NOT NULL CHECK (pass_or_fail IN (0, 1)),  -- 1=불합격(불량), 0=합격
    is_serial_conflict BOOLEAN NOT NULL DEFAULT false,  -- 같은 SerialNo 행이 둘 이상 (원본은 같은 시각에 0/1 한 쌍 - 라벨 노이즈 가능성)
    spindle_speed_max_raw        DOUBLE PRECISION NOT NULL,
    servo_current_x_max_raw      DOUBLE PRECISION NOT NULL,
    servo_current_z1_max_raw     DOUBLE PRECISION NOT NULL,
    servo_current_z2_max_raw     DOUBLE PRECISION NOT NULL,
    servo_current_z3_max_raw     DOUBLE PRECISION NOT NULL,
    servo_load_x_max_raw         DOUBLE PRECISION NOT NULL,
    servo_load_z1_max_raw        DOUBLE PRECISION NOT NULL,
    servo_load_z2_max_raw        DOUBLE PRECISION NOT NULL,
    servo_load_z3_max_raw        DOUBLE PRECISION NOT NULL,
    spindle_speed_mean_raw       DOUBLE PRECISION NOT NULL,
    servo_current_x_mean_raw     DOUBLE PRECISION NOT NULL,
    servo_current_z1_mean_raw    DOUBLE PRECISION NOT NULL,
    servo_current_z2_mean_raw    DOUBLE PRECISION NOT NULL,
    servo_current_z3_mean_raw    DOUBLE PRECISION NOT NULL,
    servo_load_x_mean_raw        DOUBLE PRECISION NOT NULL,
    servo_load_z1_mean_raw       DOUBLE PRECISION NOT NULL,
    servo_load_z2_mean_raw       DOUBLE PRECISION NOT NULL,
    servo_load_z3_mean_raw       DOUBLE PRECISION NOT NULL,
    spindle_speed_min_raw        DOUBLE PRECISION NOT NULL,
    servo_current_x_min_raw      DOUBLE PRECISION NOT NULL,
    servo_current_z1_min_raw     DOUBLE PRECISION NOT NULL,
    servo_current_z2_min_raw     DOUBLE PRECISION NOT NULL,
    servo_current_z3_min_raw     DOUBLE PRECISION NOT NULL,
    servo_load_x_min_raw         DOUBLE PRECISION NOT NULL,
    servo_load_z1_min_raw        DOUBLE PRECISION NOT NULL,
    servo_load_z2_min_raw        DOUBLE PRECISION NOT NULL,
    servo_load_z3_min_raw        DOUBLE PRECISION NOT NULL,
    spindle_speed_std_raw        DOUBLE PRECISION NOT NULL,
    servo_current_x_std_raw      DOUBLE PRECISION NOT NULL,
    servo_current_z1_std_raw     DOUBLE PRECISION NOT NULL,
    servo_current_z2_std_raw     DOUBLE PRECISION NOT NULL,
    servo_current_z3_std_raw     DOUBLE PRECISION NOT NULL,
    servo_load_x_std_raw         DOUBLE PRECISION NOT NULL,
    servo_load_z1_std_raw        DOUBLE PRECISION NOT NULL,
    servo_load_z2_std_raw        DOUBLE PRECISION NOT NULL,
    servo_load_z3_std_raw        DOUBLE PRECISION NOT NULL,
    spindle_load_max_raw         DOUBLE PRECISION NOT NULL,
    spindle_load_mean_raw        DOUBLE PRECISION NOT NULL,
    spindle_load_min_raw         DOUBLE PRECISION NOT NULL,
    spindle_load_std_raw         DOUBLE PRECISION NOT NULL,
    loaded_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (serial_no, source_received_at, pass_or_fail)  -- 재적재 시 중복 방지 (upsert 기준)
);

CREATE INDEX IF NOT EXISTS idx_machining_quality_record_received_at
    ON machining_quality_record (received_at);

-- 정밀가공 합격/불합격 배치 예측 (anomaly-worker/machining_predict.py가 학습 기간 이후 행만 예측해 덮어씀)
-- 지도 학습이라 설비 이상탐지(정상 데이터만 학습)와 별도. 원본 시각 기준으로 학습 기간과 예측 대상을 나눈다
CREATE TABLE IF NOT EXISTS machining_prediction (
    record_id        BIGINT PRIMARY KEY REFERENCES machining_quality_record (record_id) ON DELETE CASCADE,
    fail_probability DOUBLE PRECISION NOT NULL CHECK (fail_probability BETWEEN 0 AND 1),
    predicted_fail   SMALLINT NOT NULL CHECK (predicted_fail IN (0, 1)),
    model_name       VARCHAR(40) NOT NULL,
    threshold        DOUBLE PRECISION NOT NULL,
    trained_until    TIMESTAMPTZ NOT NULL,        -- 원본 시각(source_received_at) 기준 학습 구간 끝 (미포함)
    train_rows       INTEGER NOT NULL,
    train_fail       INTEGER NOT NULL,
    predicted_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
