# mini-mes

제조 설비의 생산실적·가동률(OEE)·품질 이력을 수집하고 집계하는 미니 MES(Manufacturing Execution System) 개인 프로젝트입니다.
Docker Compose → K3s/Helm → Terraform → CI/CD 순으로 인프라를 단계적으로 고도화하며 만들고 있습니다.

> **현재 상태:** 5주차 완료 (핵심 API + 설비 시뮬레이터, 불량 이력 연결, Prometheus/Grafana 모니터링, GitHub Actions CI, Helm 차트 + k3d 로컬 검증, 이상탐지 워커 + 가상 데이터 기준 성능 평가).
> Terraform, 자연어 질의는 아직 구현 전이며, K3s 서버 배포 대상도 아직 정하지 않았습니다.

프로젝트 소개 페이지(GitHub Pages, Jekyll): https://pmhllll12.github.io/mini-mes/ — 소스는 `docs/` (로컬 미리보기: `cd docs && jekyll serve --port 4002` → http://localhost:4002/mini-mes/)

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
| GET | `/anomalies?equipment_ids=&hours=` | 여러 설비(생략 시 전체) 이상탐지 결과 일괄 조회 (판정 건수, 이상 건수, 최대 점수, 이상 판정 목록) |
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
`--drift-rate`를 주면 사이클타임·불량률이 여러 구간에 걸쳐 서서히 나빠지는 점진적 열화도 만듭니다 (기본 0 = 끔, 열화 경보 평가용).

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

# 전체 설비 최근 1시간 이상탐지 결과
curl "http://localhost:8001/anomalies?hours=1"

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

## 이상탐지 (예지보전)

`anomaly-worker/`는 API와 분리된 별도 컨테이너로, 생산실적을 두 탐지기로 판정해 경보를 두 갈래로 냅니다.
설계 과정·모델별 비교·전체 평가 표는 **[이상탐지 상세 문서](docs/anomaly-detection.md)**([GitHub Pages](https://pmhllll12.github.io/mini-mes/anomaly-detection/))에 정리했습니다.

| | 급변 경보 | 열화 경보 |
|---|---|---|
| 잡는 것 | 한 구간에서 크게 튀는 이상 (공구 파손 등) | 여러 구간에 걸쳐 서서히 나빠지는 열화 (마모 등) |
| 모델 | `/models` — 설비별 Isolation Forest + 범위 이탈 robust z-score (v2), 구간 1건 특징 | `/models/drift` — v2 + 최근 5구간 이동 특징 (`--rolling-window 5`) |
| 결과 | `anomaly_result`에 기록 → `GET /anomalies` | DB에 쓰지 않음 → `mes_drift_*` 메트릭, 로그 `열화 경보 시작/해제` |
| Grafana | "이상 점수 추이", "이상 탐지 횟수" | "열화 점수 추이", "열화 경보 상태" |

- 특징: 생산실적 1건당 사이클타임, 구간당 생산 수량(`qty_good+qty_defect`), 불량률. 기준값·threshold는 모두 학습(정상) 데이터의 99% 분위수로 정합니다.
- 워커는 10초마다 아직 판정하지 않은 최근 24시간 생산실적을 판정합니다. `anomaly_result`에 log_id가 없어 생산실적의 `ts`를 그대로 쓰고 `(equipment_id, ts)`로 중복 기록을 막으며, 학습 구간(`train_end` 이전)은 판정하지 않습니다.
- 모델 파일이 없으면 죽지 않고 `모델 없음` 로그를 남기고 대기하며, 학습이 끝나면 재시작 없이 새 모델을 읽습니다. 모델은 `anomaly_models` 볼륨(`/models`)에 저장돼 컨테이너를 재시작해도 유지됩니다.
- 열화 경보는 2구간 연속 열화 판정이면 시작, 3구간 연속 정상이면 해제하고, 급변으로 판정된 구간은 판단을 보류합니다(중복 경보·깜빡임 방지).

### 학습 (정상 데이터만 사용)

모델은 **정상 데이터만으로** 학습합니다. 이상이 섞인 구간이 들어가지 않도록 `train.py`는 학습 구간 시작(`--since`)을 반드시 받습니다.

```bash
# 1) 정상 전용 시뮬레이터를 돌리기 직전 시각을 기록
TRAIN_SINCE=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# 2) 이상 비율 0으로 학습용 데이터 생성 (설비당 300건)
(cd simulator && python3 simulate.py --api-url http://localhost:8001 --interval 0 --anomaly-rate 0 --max-ticks 300)

# 3) 그 구간만으로 설비별 모델 학습 (anomaly_models 볼륨에 저장)
docker compose run --rm anomaly-worker python train.py --since "$TRAIN_SINCE"
#    열화 경보용 모델 (선택) - 없으면 급변 판정만 동작
docker compose run --rm anomaly-worker python train.py --since "$TRAIN_SINCE" --rolling-window 5 --model-dir /models/drift

# 4) 워커 로그 확인 (모델 로드 → 이후 들어오는 생산실적 판정)
docker compose logs -f anomaly-worker
```

학습 구간 안에 다른 시뮬레이터(이상 비율 > 0)가 동시에 데이터를 보내고 있으면 안 됩니다. 나중에 학습할 때는 `--until`로 정상 구간의 끝 시각도 지정합니다.

> **주의:** `docker compose down -v`는 DB 볼륨(`mes_pgdata`)과 함께 **모델 볼륨(`anomaly_models`)도 지웁니다.** 그 뒤에는 워커가 `모델 없음` 상태로 대기하므로, 위 1)~3)을 다시 실행해 재학습해야 합니다.

### 성능 평가

> **이 결과는 시뮬레이터가 만든 가상 데이터 기준이며 실제 설비 성능이 아니다.**

시뮬레이터에 `--labels-file`을 주면 `POST /production-logs` 응답의 `(equipment_id, ts)`와 이상 여부를 `simulator/labels.jsonl`(`.gitignore` 대상, DB에는 저장하지 않음)에 남기고, `evaluate/evaluate.py`가 이를 워커 판정과 `(equipment_id, ts)`로 맞춰 precision/recall/F1을 계산합니다. 학습 데이터와 평가 데이터는 **서로 다른 시뮬레이터 실행분**이고, 설계를 바꾼 뒤에는 새로 만든 실행분으로만 공식 수치를 냅니다. 평가 명령은 [상세 문서](docs/anomaly-detection.md#평가-방법)에 있습니다.

**급변 판정 (현재 모델 v2)**

| 항목 | 값 |
|---|---|
| 학습 데이터 | 설비당 300건 (총 900건), `--anomaly-rate 0` 실행분, 2026-09-28 03:36:04~03:36:12 UTC |
| 평가 데이터 | 설비당 500건 (총 1500건), `--anomaly-rate 0.2` 별도 실행분(v2 설계 이후 생성), 04:22:05~04:22:19 UTC — 학습 구간과 겹침 없음 |
| 평가 데이터 이상 비율 | 19.3% (289 / 1500) |
| threshold | 학습 데이터 점수의 99% 분위수 — EQ-001 1.0009, EQ-002 1.0002, EQ-003 1.1228 (평가 데이터로 조정하지 않음) |

| 설비 | 평가 건수 | 이상 비율 | TP | FP | FN | TN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|---|---|
| EQ-001 | 500 | 17.4% | 87 | 6 | 0 | 407 | 0.935 | 1.000 | 0.967 |
| EQ-002 | 500 | 20.2% | 101 | 11 | 0 | 388 | 0.902 | 1.000 | 0.948 |
| EQ-003 | 500 | 20.2% | 101 | 8 | 0 | 391 | 0.927 | 1.000 | 0.962 |
| **전체** | 1500 | 19.3% | 289 | 25 | 0 | 1186 | **0.920** | **1.000** | **0.959** |

- 같은 평가 데이터에서 이전 모델(v1, Isolation Forest 단독)은 P 0.812 / R 0.388 / F1 0.525였습니다. **v1의 recall이 낮았던 원인:** Isolation Forest는 학습 범위 밖의 값을 얼마나 멀리 벗어났는지와 관계없이 같은 점수로 매겨(점수 포화), 사이클타임만 크게 벗어난 이상이 정상 경계와 구분되지 않았습니다 → 범위 밖으로 멀어질수록 커지는 robust z-score를 결합해 해결.
- recall 1.000은 시뮬레이터의 급변 이상(사이클타임 2~3.5배)이 정상(±5%)에서 매우 멀리 떨어진 쉬운 이상이기 때문입니다. 평가 데이터 기준 오탐률은 약 2.1%(25/1211)입니다.

**열화 경보** (점진적 열화 모드 `--drift-rate`: 20구간 동안 사이클타임 1.0→1.3배, 불량률 3→10%)

- 점진적 열화에는 v2의 recall이 0.407로 낮고, 이동 구간 특징(열화 경보 모델)은 0.612로 더 일찍 잡습니다(진행도 50~75% 구간 recall 0.44 → 0.95). 대신 급변 이상 직후로 경보가 번져 급변 판정에 쓰면 F1이 0.970 → 0.861로 떨어지므로, 두 경보를 나눴습니다.
- 열화 경보 검증 실행분(1500건, 에피소드 19개): 에피소드 18/19 감지, 에피소드당 경보 1.00번(깜빡임 완화 전 2.05번), 첫 경보는 20구간 중 9번째(중앙값). 열화가 끝난 뒤에도 평균 약 5구간 경보가 이어지고, 열화 초반(진행도 25% 미만)은 거의 잡지 못합니다.

## 아키텍처

```
[설비 시뮬레이터] → [FastAPI 수집 API] → [PostgreSQL] ←──(급변 판정 기록)── [anomaly-worker]
                                              ↓                                   │ 급변·열화 판정
                                    [FastAPI 조회/집계 API]                       │
                                              │ /metrics                          │ /metrics(:9100, 열화 경보)
                                              └──────────→ [Prometheus] ←─────────┘
                                                                 ↓
                                                             [Grafana]
```

이후 단계에서 자연어 질의 API를 추가할 예정입니다.

## 모니터링 (Prometheus + Grafana)

`docker compose up`만으로 Prometheus·Grafana가 함께 뜨고, Grafana 대시보드가 프로비저닝 파일로 자동 구성됩니다.

- API `/metrics` (Prometheus 포맷) 노출 메트릭
  - `mes_equipment_oee`, `mes_equipment_availability`, `mes_equipment_quality_rate` — 설비별, 스크레이프 시점 기준 최근 1시간 집계
  - `mes_defect_qty` — 설비별·불량유형별, 스크레이프 시점 기준 최근 24시간 불량 수량
  - `mes_http_requests_total`, `mes_http_request_duration_seconds` — API 요청 수·지연시간 (경로·메서드·상태코드별)
- anomaly-worker `:9100/metrics` 노출 메트릭 (Prometheus가 `anomaly-worker:9100`을 스크레이프, 호스트에는 포트를 열지 않음)
  - `mes_anomaly_score` — 설비별, 직전 판정 주기에 판정한 생산실적 중 최대 이상 점수
  - `mes_anomaly_threshold`, `mes_anomaly_model_loaded` — 설비별 모델 threshold, 모델 로드 여부(1/0)
  - `mes_anomaly_scored_total`, `mes_anomaly_detected_total` — 판정 건수, 이상 판정 건수 (Counter라 워커 재시작 시 0부터 다시 셈. 누적 건수는 `/anomalies` API 기준)
  - 열화 경보: `mes_drift_score`(최근 열화 점수), `mes_drift_threshold`, `mes_drift_model_loaded`, `mes_drift_alarm`(1=경보 중), `mes_drift_detected_total`, `mes_drift_alarm_raised_total`(정상→경보 전환 횟수)
- Prometheus: http://localhost:9090 (설정: `monitoring/prometheus/prometheus.yml`, 10초 간격으로 API `/metrics` 스크레이프)
- Grafana: http://localhost:3000 (admin/admin, 로컬 전용 기본 계정) — "mini-mes 개요" 대시보드가 자동으로 로드됨
  - 이상탐지 패널: "이상 점수 추이"(설비별 점수 + 점선 threshold), "이상 탐지 횟수 (최근 1시간)", "열화 점수 추이", "열화 경보 상태"(경보 여부 + 최근 1시간 경보 횟수)
  - 프로비저닝 파일: `monitoring/grafana/provisioning/`(datasource·dashboard 등록), `monitoring/grafana/dashboards/mini-mes.json`(대시보드 정의)

## 테스트 / CI

`push`, `pull_request` 시 GitHub Actions(`.github/workflows/ci.yml`)가 다음 작업을 실행합니다.

- `test`: 의존성 설치 → 코드 문법 검사(ruff) → API 테스트(pytest). 테스트는 Postgres 서비스 컨테이너에 `db/schema.sql`을 적용한 뒤 그 위에서 동작합니다.
- `anomaly-worker-test`: ruff(`anomaly-worker`, `evaluate`) → 워커 단위 테스트(특징 추출, 점수·threshold 계산, 모델 저장/로드, 모델 없음 처리) → 평가 지표(precision/recall/F1) 단위 테스트. DB 없이 실행됩니다.
- `chart`: 차트 복사본(`files/schema.sql`, `files/grafana-dashboard.json`)이 원본과 같은지 diff로 검사 → `helm lint` → `helm template`(모니터링·워커 켬/끔 두 가지).
- `docker-build`: 위 세 작업이 통과하면 API 이미지와 anomaly-worker 이미지를 빌드합니다.

로컬에서 테스트를 돌리려면 Postgres가 필요합니다 (예: `docker compose up -d db`로 이미 띄워둔 DB를 사용해도 됩니다).
각 테스트가 넣은 행(생산실적·품질 이벤트·이상 판정, 워커가 테스트용 생산실적을 판정한 결과 포함)은 `api/tests/conftest.py`의 fixture가 테스트 종료 시 지우고, 바뀐 설비 status도 되돌리므로 개발용 DB에 테스트 데이터가 남지 않습니다. 디버깅용으로 남기려면 `KEEP_TEST_DATA=1 pytest`로 실행합니다.

```bash
cd api
pip install -r requirements.txt -r requirements-dev.txt
ruff check .
pytest -v
```

워커·평가 단위 테스트는 DB 없이 실행됩니다 (Python 3.12 기준, scikit-learn 버전 고정).

```bash
pip install -r anomaly-worker/requirements-dev.txt
ruff check anomaly-worker evaluate
(cd anomaly-worker && pytest -v)
(cd evaluate && pytest -v)
```

`oee.py`의 가동률 계산은 `run_time`이 `planned_time`을 넘어도 1.0을 넘지 않도록 상한을 두는데, `api/tests/test_oee_unit.py`에서 이 상한이 실제로 지켜지는지 단위 테스트로 검증합니다.

## K3s/Helm (로컬 검증)

`charts/mini-mes/`에 api·db·이상탐지 워커를 옮기는 Helm 차트가 있습니다. 어느 서버에 배포할지는 아직 정하지 않아, 이 단계에서는 k3d(또는 minikube)로 로컬 검증만 합니다.

- DB 접속 정보(`POSTGRES_USER`/`PASSWORD`/`DB`, `DATABASE_URL`)는 Secret(`templates/secret.yaml`)로 관리
- DB 저장소는 PersistentVolumeClaim(`templates/db-pvc.yaml`, 기본 1Gi) — Pod를 지워도 데이터가 유지되는 것까지 확인함
- api는 `/health` 기반 readiness/liveness probe 설정, DB가 연결을 받을 때까지 기다리는 initContainer(`wait-for-db`, `pg_isready`) — compose의 `depends_on: condition: service_healthy`에 해당
- anomaly-worker(`templates/anomaly-worker-*.yaml`, `values.yaml`의 `anomalyWorker.enabled`로 켜고 끔)
  - 모델은 PVC(`<release>-anomaly-models`, 기본 100Mi)의 `/models`에 저장 — 파드를 지워도 모델이 유지되는 것까지 확인함
  - 판정 중복을 막기 위해 replicas 1, PVC가 ReadWriteOnce라 `Recreate` 전략
  - 워커는 DB 연결 실패·모델 없음에도 죽지 않으므로 probe는 메트릭 포트(9100) TCP 확인. `<release>-anomaly-worker` Service로 `/metrics` 노출
- 모니터링(`templates/prometheus.yaml`, `templates/grafana.yaml`, `values.yaml`의 `monitoring.enabled`로 켜고 끔)
  - Prometheus가 api·워커 Service를 스크레이프 (워커를 끄면 대상에서도 빠짐). 저장소는 emptyDir라 파드를 지우면 메트릭 이력이 사라집니다(로컬 검증용).
  - Grafana는 docker compose와 같은 datasource(uid `mini-mes-prometheus`)·대시보드를 프로비저닝. 관리자 계정은 Secret(`<release>-grafana`)이며 기본값 admin/admin은 로컬 검증용입니다 (`--set monitoring.grafana.adminPassword=...`).
  - 설정·대시보드가 바뀌면 checksum 어노테이션으로 파드가 재시작됩니다.
- `charts/mini-mes/files/schema.sql`, `files/grafana-dashboard.json`은 각각 `db/schema.sql`, `monitoring/grafana/dashboards/mini-mes.json`의 복사본입니다(Helm이 차트 밖 파일을 직접 읽지 못해 ConfigMap용으로 넣어둠). **원본을 바꾸면 복사본도 함께 수정해야 하며, CI(`chart` 작업)가 두 파일이 다르면 실패시킵니다.**

### k3d로 검증하기

```bash
# 1) 클러스터 생성
k3d cluster create mini-mes

# 2) API·워커 이미지 빌드 후 클러스터로 반입 (레지스트리 없이 로컬 이미지를 그대로 사용)
docker build -t mini-mes-api:latest ./api
docker build -t mini-mes-anomaly-worker:latest ./anomaly-worker
k3d image import mini-mes-api:latest mini-mes-anomaly-worker:latest -c mini-mes

# 3) 차트 설치
helm install mini-mes charts/mini-mes

# 4) 파드가 뜰 때까지 대기 후 확인
kubectl get pods -w
kubectl port-forward svc/mini-mes-api 8001:8001   # docker compose가 8001을 쓰고 있으면 18001:8001 등으로 변경
curl http://localhost:8001/health

# 5) 이상탐지 모델 학습 (정상 데이터만 - 위 "학습" 절과 같은 원칙, 모델은 PVC에 저장)
TRAIN_SINCE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
(cd simulator && python3 simulate.py --api-url http://localhost:8001 --interval 0 --anomaly-rate 0 --max-ticks 300)
kubectl exec deploy/mini-mes-anomaly-worker -- python train.py --since "$TRAIN_SINCE"
kubectl exec deploy/mini-mes-anomaly-worker -- python train.py --since "$TRAIN_SINCE" --rolling-window 5 --model-dir /models/drift   # 열화 경보용 (선택)
kubectl logs deploy/mini-mes-anomaly-worker -f   # 모델 로드 → 판정 로그 확인

# 6) 모니터링 확인 (호스트의 9090/3000을 docker compose가 쓰고 있으면 다른 포트로)
kubectl port-forward svc/mini-mes-prometheus 19090:9090   # http://localhost:19090/targets 에서 api·워커 up 확인
kubectl port-forward svc/mini-mes-grafana 13000:3000      # http://localhost:13000 "mini-mes 개요" 대시보드

# 7) 정리
helm uninstall mini-mes
k3d cluster delete mini-mes
```

minikube를 쓴다면 2)의 `k3d image import` 대신 `minikube image load mini-mes-api:latest mini-mes-anomaly-worker:latest`를 사용하면 됩니다.

**확인된 동작:** 차트 0.3.0까지는 api가 시작할 때 DB에 연결하는데 DB 파드가 아직 준비되지 않아 api 파드가 3회 재시작됐습니다. 0.3.1에서 `wait-for-db` initContainer를 추가한 뒤에는 initContainer가 `no response` 동안 기다렸다가 `accepting connections` 이후 api를 시작해 **RESTARTS 0**, api 로그의 DB 연결 오류 0건을 k3d에서 확인했습니다. anomaly-worker는 DB 연결 실패를 로그로 남기고 재시도하므로 원래부터 재시작 없이 정상화됩니다.

**k3d 검증 결과** (2026-09-28, k3d v5.7.4 / k3s v1.30.4 / Helm v3.16.2, 차트 0.3.0)

- 워커: 설치 직후 `모델 없음` 대기 → `kubectl exec`로 정상 데이터 학습 → 이상 섞인 데이터 판정·기록 → 워커 파드를 지워도 새 파드가 PVC에서 모델을 다시 읽고 이미 판정한 건은 다시 기록하지 않음
- 모니터링: 파드 5개(db·api·워커·Prometheus·Grafana) Running, 스크레이프 대상 api·워커 모두 up, Grafana에 대시보드(패널 9개) 로드·조회 성공
- 정적 검증: `helm lint` 통과, 렌더링된 리소스 18개 kubeconform(strict) 통과 (`monitoring.enabled=false`면 10개)

## 기술 스택

- 현재: Python, FastAPI, SQLAlchemy, PostgreSQL 16, Docker Compose, Prometheus, Grafana, GitHub Actions, Helm/k3d(로컬 검증), scikit-learn(Isolation Forest)
- 예정: Terraform, K3s 서버 배포, Gemini(function calling)

## 로드맵

| 주차 | 내용 | 상태 |
|---|---|---|
| 1주 | 스키마 설계, FastAPI 수집/조회 API, 시뮬레이터, 다중 설비 조회·CSV export | ✅ |
| 2주 | `quality_event`에 `production_log_id`(nullable FK) 추가, 설비별·불량유형별 불량 집계 API(`/quality/defect-summary`), 시뮬레이터가 불량 발생 시 연결된 품질 이벤트도 함께 전송, 설비 status 자동 갱신 | ✅ |
| 3주 | Prometheus + Grafana 모니터링 스택 추가 (`/metrics`, 대시보드 프로비저닝) | ✅ |
| 4주 | Helm 차트 작성 + k3d 로컬 검증 (Secret/PVC/probe, 서버 배포 대상은 미정). 5주차 이후 anomaly-worker(Deployment·모델 PVC·메트릭 Service), Prometheus·Grafana도 차트에 추가 | ✅ |
| 5주 | 이상탐지(예지보전) 워커: 설비별 Isolation Forest + robust z-score(정상 데이터만 학습), `/anomalies` API, 급변·열화 경보 분리, 워커 메트릭·Grafana 패널, 시뮬레이터 라벨 기반 성능 평가(가상 데이터 기준 급변 F1 v1 0.525 → v2 0.959), 워커 단위 테스트·CI | ✅ |
| 6주 | 자연어 질의 API 추가 | |
| 7주 | Terraform, 문서화·데모 영상 (GitHub Actions CI는 완료) | |

## 알려진 한계

- 데이터는 시뮬레이터가 만든 가상 데이터이며 실제 설비 데이터가 아닙니다.
- `db/schema.sql`은 DB 최초 생성 시 한 번만 적용됩니다. 스키마를 바꾸면 `docker compose down -v` 후 다시 띄워야 합니다.
- 이상탐지 관련 (상세는 [이상탐지 문서](docs/anomaly-detection.md#알려진-한계))
  - 성능 수치는 시뮬레이터가 만든 가상 데이터 기준이며, 시뮬레이터의 이상 패턴은 실제 고장 양상보다 단순합니다. 급변 recall 1.000은 쉬운 이상이라 나온 수치입니다.
  - 특징이 단순합니다(생산실적 1건 단위 3개 + 열화용 이동 구간 2개, 센서 데이터 없음). 점진적 열화 초반(진행도 25% 미만)은 거의 잡지 못합니다.
  - 설비별 모델이라 새 설비를 추가하거나 공정 조건이 바뀌면 재학습이 필요하고(자동 재학습 없음), 학습 데이터가 정상인지는 사람이 학습 구간을 지정해서 보장합니다.
  - 열화 경보는 DB에 남지 않고(스키마 유지) 메트릭으로만 남으며, 열화가 끝난 뒤에도 몇 구간 더 켜져 있습니다.
  - `anomaly_result`에 생산실적 ID가 없어 `(equipment_id, ts)`로 같은 로그인지 판단합니다.
- Helm 차트(워커·Prometheus·Grafana 포함)는 k3d 로컬 검증까지만 했고, 차트의 Prometheus 저장소는 emptyDir라 파드가 재시작되면 메트릭 이력이 사라집니다.

## 개발 기간

2026.09 ~ 진행 중