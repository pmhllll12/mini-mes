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
