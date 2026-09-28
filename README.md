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

## 이상탐지 워커 (예지보전)

`anomaly-worker/`는 API와 분리된 별도 컨테이너(`docker compose`의 `anomaly-worker` 서비스)입니다.

- 특징(`features.py`): 생산실적 1건당 사이클타임, 구간당 생산 수량(`qty_good+qty_defect`), 불량률
- 모델(`model.py`): 설비별 Isolation Forest. `anomaly_score = -score_samples()`(클수록 이상), threshold는 학습(정상) 데이터 점수의 99% 분위수
- 워커(`worker.py`): 10초마다 설비별로 아직 판정하지 않은 최근 24시간 생산실적을 추론해 `anomaly_result`에 `anomaly_score`, `is_anomaly`를 기록
  - `anomaly_result`에는 log_id가 없으므로 `ts`에 생산실적의 `ts`를 그대로 넣고, `(equipment_id, ts)`가 이미 있으면 기록하지 않아 중복을 막습니다.
  - 학습에 쓴 구간(모델의 `train_end` 이전)은 추론하지 않습니다.
  - 모델 파일이 없으면 죽지 않고 `모델 없음: ...` 로그를 남기고 대기하며, 학습이 끝나면 재시작 없이 새 모델을 읽습니다.
- 모델 저장: `anomaly_models` 볼륨의 `/models/{equipment_id}.joblib` — 컨테이너를 재시작하거나 다시 빌드해도 유지됩니다.

### 학습 (정상 데이터만 사용)

Isolation Forest는 **정상 데이터만으로** 학습합니다. 이상이 섞인 구간이 들어가지 않도록 `train.py`는 학습 구간 시작(`--since`)을 반드시 받습니다.

```bash
# 1) 정상 전용 시뮬레이터를 돌리기 직전 시각을 기록
TRAIN_SINCE=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# 2) 이상 비율 0으로 학습용 데이터 생성 (설비당 300건)
cd simulator
python3 simulate.py --api-url http://localhost:8001 --interval 0 --anomaly-rate 0 --max-ticks 300
cd ..

# 3) 그 구간만으로 설비별 모델 학습 (결과는 anomaly_models 볼륨에 저장)
docker compose run --rm anomaly-worker python train.py --since "$TRAIN_SINCE"

# 4) 워커 로그 확인 (모델 로드 → 이후 들어오는 생산실적 판정)
docker compose logs -f anomaly-worker
```

학습 구간 안에 다른 시뮬레이터(이상 비율 > 0)가 동시에 데이터를 보내고 있으면 안 됩니다. 필요하면 `--until`로 끝 시각도 지정할 수 있습니다.

> **주의:** `docker compose down -v`는 DB 볼륨(`mes_pgdata`)과 함께 **모델 볼륨(`anomaly_models`)도 지웁니다.** 그 뒤에는 워커가 `모델 없음` 상태로 대기하므로, 위 1)~3)을 다시 실행해 재학습해야 합니다.

### 성능 평가

> **이 결과는 시뮬레이터가 만든 가상 데이터 기준이며 실제 설비 성능이 아니다.**

시뮬레이터에 `--labels-file`을 주면 `POST /production-logs` 응답의 `(equipment_id, ts, log_id)`와 이상 여부를 실행 단위(`run_id`)로 `simulator/labels.jsonl`에 기록합니다 (DB에는 저장하지 않으며, 이 파일은 `.gitignore` 대상). `evaluate/evaluate.py`가 이 라벨과 `anomaly_result`를 `(equipment_id, ts)`로 맞춰 설비별·전체 precision/recall/F1을 계산합니다.

학습 데이터와 평가 데이터는 **서로 다른 시뮬레이터 실행분**입니다. 워커는 모델의 학습 구간(`train_end`) 이후 데이터만 판정하고, 평가 스크립트도 두 구간이 겹치는지 검사해 출력합니다.

```bash
# 1) 학습용 (정상만) - 라벨도 남겨 이상 0건임을 기록
TRAIN_SINCE=$(date -u +%Y-%m-%dT%H:%M:%SZ)
(cd simulator && python3 simulate.py --api-url http://localhost:8001 --interval 0 --anomaly-rate 0 --max-ticks 300 --labels-file labels.jsonl)
docker compose run --rm anomaly-worker python train.py --since "$TRAIN_SINCE"

# 2) 평가용 - 별도 실행, 이상 비율 0.2 (워커가 새 모델을 읽을 때까지 10초 이상 기다린 뒤 실행)
(cd simulator && python3 simulate.py --api-url http://localhost:8001 --interval 0 --anomaly-rate 0.2 --max-ticks 500 --labels-file labels.jsonl)

# 3) 워커 판정이 끝난 뒤(10~20초) 평가 - 라벨 파일의 마지막 실행분을 평가 (--run-id로 지정 가능)
docker compose run --rm -v "$PWD/evaluate:/eval" -v "$PWD/simulator:/sim:ro" \
    anomaly-worker python /eval/evaluate.py --labels /sim/labels.jsonl
```

**평가 조건** (2026-09-28 실행)

| 항목 | 값 |
|---|---|
| 학습 데이터 | 설비당 300건 (총 900건), `--anomaly-rate 0` 실행분, 03:36:04~03:36:12 UTC |
| 평가 데이터 | 설비당 500건 (총 1500건), `--anomaly-rate 0.2` 별도 실행분, 03:36:39~03:36:54 UTC (학습 구간과 겹침 없음) |
| 평가 데이터 이상 비율 | 21.5% (322 / 1500) |
| threshold | 학습 데이터 점수의 99% 분위수 — EQ-001 0.6979, EQ-002 0.7100, EQ-003 0.6828 (평가 데이터로 조정하지 않음) |

**결과**

| 설비 | 평가 건수 | 이상 비율 | TP | FP | FN | TN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|---|---|
| EQ-001 | 500 | 21.6% | 48 | 3 | 60 | 389 | 0.941 | 0.444 | 0.604 |
| EQ-002 | 500 | 21.2% | 39 | 5 | 67 | 389 | 0.886 | 0.368 | 0.520 |
| EQ-003 | 500 | 21.6% | 59 | 9 | 49 | 383 | 0.868 | 0.546 | 0.670 |
| **전체** | 1500 | 21.5% | 146 | 17 | 176 | 1161 | **0.896** | **0.453** | **0.602** |

**Recall이 낮은 원인:** Isolation Forest는 학습 범위 밖의 값을 "얼마나 멀리 벗어났는지"와 관계없이 같은 점수로 매겨서(사이클타임이 1.3배든 3배든 동일), 이상 데이터의 점수가 정상 경계 데이터의 점수(학습 최대 약 0.77)와 구분되지 않고, 불량률까지 벗어난 경우만 threshold를 넘습니다.

## 아키텍처

```
[설비 시뮬레이터] → [FastAPI 수집 API] → [PostgreSQL]
                                              ↓
                                    [FastAPI 조회/집계 API] → /metrics → [Prometheus] → [Grafana]
                                              ↑                                  ↑
                          [anomaly-worker] ──(anomaly_result 기록)          /metrics(:9100)
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
- Prometheus: http://localhost:9090 (설정: `monitoring/prometheus/prometheus.yml`, 10초 간격으로 API `/metrics` 스크레이프)
- Grafana: http://localhost:3000 (admin/admin, 로컬 전용 기본 계정) — "mini-mes 개요" 대시보드가 자동으로 로드됨
  - 이상탐지 패널: "이상 점수 추이"(설비별 점수 + 점선 threshold), "이상 탐지 횟수 (최근 1시간)"
  - 프로비저닝 파일: `monitoring/grafana/provisioning/`(datasource·dashboard 등록), `monitoring/grafana/dashboards/mini-mes.json`(대시보드 정의)

## 테스트 / CI

`push`, `pull_request` 시 GitHub Actions(`.github/workflows/ci.yml`)가 다음 작업을 실행합니다.

- `test`: 의존성 설치 → 코드 문법 검사(ruff) → API 테스트(pytest). 테스트는 Postgres 서비스 컨테이너에 `db/schema.sql`을 적용한 뒤 그 위에서 동작합니다.
- `anomaly-worker-test`: ruff(`anomaly-worker`, `evaluate`) → 워커 단위 테스트(특징 추출, 점수·threshold 계산, 모델 저장/로드, 모델 없음 처리) → 평가 지표(precision/recall/F1) 단위 테스트. DB 없이 실행됩니다.
- `docker-build`: 위 두 작업이 통과하면 API 이미지와 anomaly-worker 이미지를 빌드합니다.

로컬에서 테스트를 돌리려면 Postgres가 필요합니다 (예: `docker compose up -d db`로 이미 띄워둔 DB를 사용해도 됩니다).

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

`charts/mini-mes/`에 api·db를 옮기는 Helm 차트가 있습니다. 어느 서버에 배포할지는 아직 정하지 않아, 이 단계에서는 k3d(또는 minikube)로 로컬 검증만 합니다.

- DB 접속 정보(`POSTGRES_USER`/`PASSWORD`/`DB`, `DATABASE_URL`)는 Secret(`templates/secret.yaml`)로 관리
- DB 저장소는 PersistentVolumeClaim(`templates/db-pvc.yaml`, 기본 1Gi) — Pod를 지워도 데이터가 유지되는 것까지 확인함
- api는 `/health` 기반 readiness/liveness probe 설정
- `charts/mini-mes/files/schema.sql`은 `db/schema.sql`의 복사본입니다(Helm이 차트 밖 파일을 직접 읽지 못해 ConfigMap용으로 넣어둠). **스키마를 바꾸면 두 파일을 함께 수정해야 합니다.**

### k3d로 검증하기

```bash
# 1) 클러스터 생성
k3d cluster create mini-mes

# 2) API 이미지 빌드 후 클러스터로 반입 (레지스트리 없이 로컬 이미지를 그대로 사용)
docker build -t mini-mes-api:latest ./api
k3d image import mini-mes-api:latest -c mini-mes

# 3) 차트 설치
helm install mini-mes charts/mini-mes

# 4) 파드가 뜰 때까지 대기 후 확인
kubectl get pods -w
kubectl port-forward svc/mini-mes-api 8001:8001
curl http://localhost:8001/health

# 5) 정리
helm uninstall mini-mes
k3d cluster delete mini-mes
```

minikube를 쓴다면 2)의 `k3d image import` 대신 `minikube image load mini-mes-api:latest`를 사용하면 됩니다.

**확인된 동작:** DB 파드가 준비되기 전에 api 파드가 DB 연결 실패로 몇 차례 재시작될 수 있는데(readiness/liveness probe와 별개로, 앱이 시작 시 DB에 연결을 시도하기 때문), Kubernetes가 자동으로 재시도하면서 DB가 준비되면 정상화됩니다. `docker-compose.yml`의 `depends_on: condition: service_healthy`에 해당하는 대기 로직은 아직 차트에 없습니다.

## 기술 스택

- 현재: Python, FastAPI, SQLAlchemy, PostgreSQL 16, Docker Compose, Prometheus, Grafana, GitHub Actions, Helm/k3d(로컬 검증), scikit-learn(Isolation Forest)
- 예정: Terraform, K3s 서버 배포, Gemini(function calling)

## 로드맵

| 주차 | 내용 | 상태 |
|---|---|---|
| 1주 | 스키마 설계, FastAPI 수집/조회 API, 시뮬레이터, 다중 설비 조회·CSV export | ✅ |
| 2주 | `quality_event`에 `production_log_id`(nullable FK) 추가, 설비별·불량유형별 불량 집계 API(`/quality/defect-summary`), 시뮬레이터가 불량 발생 시 연결된 품질 이벤트도 함께 전송, 설비 status 자동 갱신 | ✅ |
| 3주 | Prometheus + Grafana 모니터링 스택 추가 (`/metrics`, 대시보드 프로비저닝) | ✅ |
| 4주 | Helm 차트 작성 + k3d 로컬 검증 (Secret/PVC/probe, 서버 배포 대상은 미정) | ✅ |
| 5주 | 이상탐지(예지보전) 워커 추가: 설비별 Isolation Forest(정상 데이터만 학습, 모델은 볼륨 저장), `/anomalies` 조회 API, 워커 메트릭·Grafana "이상 점수 추이" 패널, 시뮬레이터 라벨 기반 성능 평가(가상 데이터 기준 전체 F1 0.602), 워커 단위 테스트·이미지 빌드 CI | ✅ |
| 6주 | 자연어 질의 API 추가 | |
| 7주 | Terraform, 문서화·데모 영상 (GitHub Actions CI는 완료) | |

## 알려진 한계

- 데이터는 시뮬레이터가 만든 가상 데이터이며 실제 설비 데이터가 아닙니다.
- `db/schema.sql`은 DB 최초 생성 시 한 번만 적용됩니다. 스키마를 바꾸면 `docker compose down -v` 후 다시 띄워야 합니다.
- 이상탐지 관련
  - 성능 평가는 시뮬레이터가 만든 가상 데이터 기준이며 실제 설비 성능이 아닙니다. 시뮬레이터의 이상 패턴(사이클타임 2~3.5배 + 불량률 20~40%)은 실제 고장 양상보다 단순합니다.
  - 특징이 단순합니다 (생산실적 1건 단위의 사이클타임·생산 수량·불량률 3개). 시간 흐름(추세, 이동평균)이나 센서 데이터(진동·온도 등)는 쓰지 않습니다. 구간당 생산량이 적은 설비(EQ-001, 약 4~5개)는 불량률이 0%와 25% 사이를 오가서 불량률 특징의 변별력이 낮습니다.
  - Isolation Forest는 학습 범위를 벗어난 정도에 따라 점수가 커지지 않아(점수 포화) recall이 낮습니다 (전체 0.453, 위 "성능 평가" 참고).
  - 설비별 모델이라 새 설비를 추가하면 그 설비의 정상 데이터를 모아 재학습해야 하며, 그 전까지 해당 설비는 `모델 없음` 상태로 판정되지 않습니다. 공정 조건이 바뀌어 정상 범위가 달라져도 재학습이 필요합니다(자동 재학습 없음).
  - 학습 데이터가 정상인지는 사람이 학습 구간(`--since`/`--until`)을 지정해서 보장합니다. 실제 현장에서는 "정상만 있는 구간"을 확보하기 어렵습니다.
  - `anomaly_result`에 생산실적 ID가 없어 `(equipment_id, ts)`로 같은 로그인지 판단합니다. 같은 설비에 같은 ts의 로그가 두 건 이상 들어오면 한 건만 판정됩니다.
  - Helm 차트에는 아직 anomaly-worker가 없습니다 (Docker Compose에서만 동작).

## 개발 기간

2026.09 ~ 진행 중