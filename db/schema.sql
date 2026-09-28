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
    event_id        BIGSERIAL PRIMARY KEY,
    equipment_id    VARCHAR(20) NOT NULL REFERENCES equipment(equipment_id),
    ts              TIMESTAMPTZ NOT NULL DEFAULT now(),
    defect_type     VARCHAR(50) NOT NULL,
    severity        VARCHAR(10) NOT NULL DEFAULT 'low', -- low / medium / high
    note            TEXT
);

CREATE INDEX IF NOT EXISTS idx_quality_event_equipment_ts
    ON quality_event (equipment_id, ts DESC);

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

-- 초기 설비 3대 시드 데이터
INSERT INTO equipment (equipment_id, name, line_id, status) VALUES
    ('EQ-001', 'CNC 가공기 1호', 'LINE-A', 'idle'),
    ('EQ-002', 'CNC 가공기 2호', 'LINE-A', 'idle'),
    ('EQ-003', '포장 설비 1호', 'LINE-B', 'idle')
ON CONFLICT (equipment_id) DO NOTHING;
