# mini-mes

제조 설비의 생산실적·가동률(OEE)·품질 이력을 수집하고 집계하는 미니 MES(Manufacturing Execution System) 개인 프로젝트입니다.
Docker Compose → K3s/Helm → Terraform → CI/CD 순으로 인프라를 단계적으로 고도화하며 만들고 있습니다.

> **현재 상태:** 3주차 완료 (핵심 API + 설비 시뮬레이터, 불량 이력 연결, Prometheus/Grafana 모니터링, Docker Compose로 실행).
> Terraform, CI/CD, 이상탐지, 자연어 질의는 아직 구현 전입니다.

## 배경

제조 현장에서 수율 데이터 관리와 가공 품질 관리를 담당하며, MES에서 데이터를 뽑는 일이 가장 불편했습니다.
설비마다 화면을 따로 열어 조건을 걸고, 다운로드한 뒤 엑셀에서 다시 합치고 가공해야 했습니다.
이 경험을 바탕으로, 그 데이터를 다루는 시스템을 직접 설계해 보는 프로젝트입니다.

팀 프로젝트 [SUPERSUB](https://github.com/pmhllll12/super-sub.cloud)에서 FastAPI·K3s 기반 서비스의
백엔드/DB 작업에 참여한 경험을 이어, 이 프로젝트에서는 인프라 구성과 배포 자동화를 처음부터 직접 설계하는 것을 목표로 합니다.

## 실무 불편함 → 설계 반영

| 실무에서 겪은 불편함 | 이 프로젝트의 해결 | 상태 |
|---|---|---|
| 화면에서 기간·설비 조건을 매번 수동 설정 | API 파라미터로 조건 지정 | 구현 |
| 설비를 하나씩 따로 조회 | `equipment_ids` 다중 지정, 생략 시 전체 설비 일괄 조회 | 구현 |
| 다운로드 후 엑셀에서 재가공 | 조건에 맞는 CSV를 바로 내려받는 export API | 구현 |
| 사람이 매번 조작해야 해서 자동화 불가 | REST API 제공 | 구현 |
| 원하는 정보를 말로 묻고 싶음 | 자연어 질의 (LLM function calling) | 예정 |
| 같은 리포트를 반복해서 수동 추출 | 예약 리포트 자동 발송 | 예정 |

## 구현된 API

| 메서드 | 경로 | 설명 |
|---|---|---|
| GET | `/health` | 상태 확인 |
| GET | `/equipment`, `/equipment/{id}` | 설비 목록·상세 조회 (status 자동 갱신, 아래 규칙 참고) |
| POST | `/production-logs` | 생산실적 수집 |
| POST | `/quality-events` | 품질 이벤트(불량 유형·심각도) 수집 |
| GET | `/equipment/{id}/oee?hours=` | 단일 설비 OEE |
| GET | `/oee?equipment_ids=&hours=` | 여러 설비(생략 시 전체) OEE 일괄 조회 |
| GET | `/quality/defect-summary?equipment_ids=&hours=` | 설비별·불량유형별 불량 집계 |
| GET | `/export/production-logs?equipment_ids=&start=&end=` | 생산실적 CSV 다운로드 |

## 설비 status 규칙

생산실적 수신 시 `qty_good+qty_defect`가 0보다 크면 `running`, 0이면 `stopped`로 갱신하며, 마지막 생산실적 이후 5분 이상 새 데이터가 없으면 조회 시점에 `stopped`로 표시한다 (한 번도 생산실적이 없었던 설비는 시드 상태인 `idle`을 유지).

## OEE 계산

OEE = 가동률 × 양품률 (성능 가동률은 제외한 단순화 버전)

- 실제 가동시간 = 사이클타임(제품 1개당 초) × 생산 수량. 구간별 계획시간을 상한으로 적용
- 가동률 = 실제 가동시간 합 / 계획시간 합 (항상 0~1)
- 양품률 = 양품 수 / (양품 + 불량)

## 실행

Docker와 Docker Compose가 필요합니다.

```bash
git clone https://github.com/pmhllll12/mini-mes.git
cd mini-mes
docker compose up --build -d
```

- API 문서(Swagger): http://localhost:8001/docs
- Prometheus: http://localhost:9090 / Grafana: http://localhost:3000 (admin/admin)
- 호스트 포트는 `docker-compose.yml`의 `8001:8000`에서 바꿀 수 있습니다.

### 설비 시뮬레이터

실제 설비 데이터가 아니라, 정상 패턴과 이상 패턴을 섞어 생성하는 가상 데이터입니다.
1회 전송이 `--window-sec`초 분량의 생산실적이고, 생산 수량은 사이클타임과 구간 길이에서 계산합니다.
불량이 발생한 생산실적 건에는 해당 `production_log_id`를 가리키는 품질 이벤트(불량 유형: scratch/dimension_out/burr/discoloration)를 함께 전송하며, ANOMALY 구간에서는 `dimension_out` 비중이 높아지도록 가중치를 다르게 둡니다.

```bash
cd simulator
python3 simulate.py --api-url http://localhost:8001 --interval 2 --anomaly-rate 0.2 --max-ticks 30
```

### 조회 예시

```bash
# 전체 설비 OEE를 한 번에
curl "http://localhost:8001/oee?hours=1"

# 특정 설비 2개만
curl "http://localhost:8001/oee?equipment_ids=EQ-001&equipment_ids=EQ-003&hours=1"

# 전체 설비 생산실적을 CSV로
curl "http://localhost:8001/export/production-logs?start=2020-01-01T00:00:00Z" -o report.csv
```

```json
{
  "equipment_id": "EQ-001",
  "availability": 0.8865,
  "quality_rate": 0.9375,
  "oee": 0.8311,
  "total_qty": 112,
  "total_defect": 7
}
```

## 아키텍처

```
[설비 시뮬레이터] → [FastAPI 수집 API] → [PostgreSQL]
                                              ↓
                                    [FastAPI 조회/집계 API] → /metrics → [Prometheus] → [Grafana]
```

이후 단계에서 이상탐지 워커, 자연어 질의 API를 추가할 예정입니다.

## 모니터링 (Prometheus + Grafana)

`docker compose up`만으로 Prometheus·Grafana가 함께 뜨고, Grafana 대시보드가 프로비저닝 파일로 자동 구성됩니다.

- API `/metrics` (Prometheus 포맷) 노출 메트릭
  - `mes_equipment_oee`, `mes_equipment_availability`, `mes_equipment_quality_rate` — 설비별, 스크레이프 시점 기준 최근 1시간 집계
  - `mes_defect_qty` — 설비별·불량유형별, 스크레이프 시점 기준 최근 24시간 불량 수량
  - `mes_http_requests_total`, `mes_http_request_duration_seconds` — API 요청 수·지연시간 (경로·메서드·상태코드별)
- Prometheus: http://localhost:9090 (설정: `monitoring/prometheus/prometheus.yml`, 10초 간격으로 API `/metrics` 스크레이프)
- Grafana: http://localhost:3000 (admin/admin, 로컬 전용 기본 계정) — "mini-mes 개요" 대시보드가 자동으로 로드됨
  - 프로비저닝 파일: `monitoring/grafana/provisioning/`(datasource·dashboard 등록), `monitoring/grafana/dashboards/mini-mes.json`(대시보드 정의)

## 기술 스택

- 현재: Python, FastAPI, SQLAlchemy, PostgreSQL 16, Docker Compose, Prometheus, Grafana
- 예정: K3s, Helm, Terraform, GitHub Actions, scikit-learn(Isolation Forest), Gemini(function calling)

## 로드맵

| 주차 | 내용 | 상태 |
|---|---|---|
| 1주 | 스키마 설계, FastAPI 수집/조회 API, 시뮬레이터, 다중 설비 조회·CSV export | ✅ |
| 2주 | `quality_event`에 `production_log_id`(nullable FK) 추가, 설비별·불량유형별 불량 집계 API(`/quality/defect-summary`), 시뮬레이터가 불량 발생 시 연결된 품질 이벤트도 함께 전송, 설비 status 자동 갱신 | ✅ |
| 3주 | Prometheus + Grafana 모니터링 스택 추가 (`/metrics`, 대시보드 프로비저닝) | ✅ |
| 4주 | K3s/Helm 배포 전환 | |
| 5주 | 이상탐지(예지보전) 워커 추가 | |
| 6주 | 자연어 질의 API 추가 | |
| 7주 | Terraform, CI/CD, 문서화·데모 영상 | |

## 알려진 한계

- 데이터는 시뮬레이터가 만든 가상 데이터이며 실제 설비 데이터가 아닙니다.
- `db/schema.sql`은 DB 최초 생성 시 한 번만 적용됩니다. 스키마를 바꾸면 `docker compose down -v` 후 다시 띄워야 합니다.

## 개발 기간

2026.09 ~ 진행 중