# mini-mes

제조 설비의 생산실적·가동률(OEE)·품질 이력을 수집하고 집계하는 미니 MES(Manufacturing Execution System) 개인 프로젝트입니다.
Docker Compose → K3s/Helm → Terraform → CI/CD 순으로 인프라를 단계적으로 고도화하며 만들고 있습니다.

> **현재 상태:** 6주차 진행 중 (핵심 API + 설비 시뮬레이터, 불량 이력 연결, Prometheus/Grafana 모니터링, GitHub Actions CI, Helm 차트 + k3d 로컬 검증, 이상탐지 워커 + 가상 데이터 기준 성능 평가, 자연어 질의 API — Gemini로 부분 평가, Claude 미평가).
> 7주차: K3s 서버는 Oracle Cloud 상시 무료 ARM VM(오사카)으로 정하고 Terraform 코드를 작성했습니다. 네트워크는 생성됐지만 VM은 무료 ARM 재고 부족("Out of host capacity")으로 아직 생성 대기 중입니다.

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
| 원하는 정보를 말로 묻고 싶음 | 자연어 질의 `POST /query` (LLM function calling, 읽기 전용 도구) | 구현 |
| 같은 리포트를 반복해서 수동 추출 | 예약 리포트: 매일 새벽 전날 설비별 요약을 DB에 스냅샷으로 저장(K8s CronJob), 기간·설비 지정 조회·CSV (메일 발송은 미구현) | 구현 |

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
| POST | `/reports/daily?date=` | 일일 리포트 생성·저장 (KST 하루, 생략 시 어제, 끝난 날짜만, 같은 날짜는 덮어씀). Helm CronJob이 매일 00:10 KST 호출 |
| GET | `/reports/daily?equipment_ids=&start_date=&end_date=&format=json\|csv` | 저장된 일일 리포트 조회 (기본 최근 7일): 설비별 가동률·양품률·OEE, 생산·불량 수량, 최다 불량 유형(건수·수량), 급변 이상 건수, 열화 경보 횟수·시간 |
| GET | `/drift-alarms?equipment_ids=&hours=&format=json\|csv` | 기간과 겹치는 열화 경보 이력 (시작·해제 시각, 진행 중 여부, 지속 시간, 시작 점수), `format=csv`면 바로 내려받기 |
| GET | `/export/production-logs?equipment_ids=&start=&end=` | 생산실적 CSV 다운로드 |
| POST | `/query` | 자연어 질의 — LLM이 읽기 전용 도구를 호출해 답변 + 근거(호출한 도구·인자·결과) 반환 (API 키 필요) |

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

# 일일 리포트: 어제(KST) 분 생성 → 최근 7일을 CSV로 (Helm에서는 CronJob이 매일 00:10에 생성, compose에서는 직접 호출)
curl -X POST "http://localhost:8001/reports/daily"
curl "http://localhost:8001/reports/daily?format=csv" -o daily_report.csv
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
| 결과 | `anomaly_result`에 기록 → `GET /anomalies` | 경보 시작/해제를 `drift_alarm`에 기록 → `GET /drift-alarms`, 판정 건별 점수는 `mes_drift_*` 메트릭 |
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

## 자연어 질의 (LLM function calling)

`POST /query`에 질문을 보내면 LLM이 **읽기 전용 도구**를 골라 호출하고, 그 결과로 답합니다. LLM은 SQL을 만들지 않습니다.
설계·평가·발견한 문제는 **[자연어 질의 상세 문서](docs/nlq.md)**([GitHub Pages](https://pmhllll12.github.io/mini-mes/nlq/))에 정리했습니다.

```bash
curl -X POST http://localhost:8001/query -H 'Content-Type: application/json' \
     -d '{"question": "최근 1시간 EQ-002에서 가장 많이 나온 불량 유형은?"}'
# -> answer + tool_calls: [{"name": "get_defect_summary", "input": {"equipment_ids": ["EQ-002"], "start": "...", "end": "..."}, "ok": true, "result": {...}}]
```

- 도구 4개: `list_equipment`, `get_oee`, `get_defect_summary`, `get_anomalies` — 기존 조회 API와 같은 계산 코드를 재사용 (`api/nlq_tools.py`)
- 인자 검증: 등록된 설비만, 시간대가 있는 ISO 8601, 최대 30일. 잘못된 인자는 오류 메시지를 LLM에 돌려줘 스스로 고치게 합니다.
- 응답에 **호출한 도구·인자·결과**를 함께 돌려줘 답변의 근거를 확인할 수 있습니다. 도구 호출은 최대 3라운드, 그 뒤에는 도구 없이 답변만 받습니다.
- 제공자 두 가지 (`api/nlq_providers.py`): Claude(`claude-opus-5`, strict 도구, 거절 시 서버측 `fallbacks="default"`) / Gemini(`gemini-flash-latest`, 수동 function calling). 요청의 `provider`, 환경변수 `NLQ_PROVIDER`, 키가 있는 제공자 순으로 고릅니다.
- **API 키는 `.env`에만** 넣습니다: `cp .env.example .env` 후 `ANTHROPIC_API_KEY` / `GEMINI_API_KEY` 입력 (`.env`는 커밋되지 않음). 키가 없으면 `/query`만 503이고 다른 API는 그대로 동작합니다.
- 메트릭: `mes_nlq_requests_total{provider,outcome}`, `mes_nlq_tool_calls_total{provider,tool,ok}` — Grafana "자연어 질의 요청", "자연어 질의 도구 호출" 패널

**평가** (`evaluate/nlq_eval.py`, 질문 12개): 도구 선택, 설비·기간 인자, 도구 결과를 답변에 그대로 전했는지(근거), 없는 설비·조회 불가 항목·범위 밖 질문에 추측 없이 안내하는지를 채점합니다.

| 제공자 · 모델 (2026-09-28) | 평가 완료 | 통과 | 도구 선택 | 설비 인자 | 기간 인자 | 근거 | 안내 문구 | 평균 응답 |
|---|---|---|---|---|---|---|---|---|
| Gemini · `gemini-2.5-flash` | 8 / 12 | **8 / 8** | 7/7 | 6/6 | 6/6 | 3/3 | 1/1 | 4.5초 |
| Claude · `claude-opus-5` | 미평가 (API 크레딧 없음) | | | | | | | |

- **미평가 4개**(LINE-B 설비, 두 설비 이상 비교, 없는 설비 EQ-009, 범위 밖 예측 질문)는 Gemini 무료 등급 하루 요청 한도(모델당 20회)에 걸려 요청 자체가 실패(429)했습니다. 모델 오답이 아니며, 기본 모델 `gemini-flash-latest`(= gemini-3.8-flash)도 같은 날 한도를 다 써서 `gemini-2.5-flash`로 평가했습니다.
- 답변 수치를 조회 API로 직접 대조: "최근 24시간 이상 최다 설비 EQ-003, 626건"은 일치. "오늘 EQ-002 최다 불량 dimension_out 217건, scratch도 217건으로 동일"은 **217이 불량 수량(개)인데 건수처럼 표현**했고 실제 이벤트 건수는 213 vs 204라 동률이 아님 — 자동 채점(정답 유형 포함 여부)은 통과했지만 수치 표현은 부정확했습니다.
- 실제 API 호출로 발견해 고친 점: Gemini에 함수 결과를 `role="tool"`로 보내면 400(SDK README 예제와 다름) → `role="user"`로 전송, 일시 과부하(503) 대비 재시도.

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
  - 자연어 질의 패널: "자연어 질의 요청 (최근 1시간, 제공자·결과별)", "자연어 질의 도구 호출 (최근 1시간)"
  - 프로비저닝 파일: `monitoring/grafana/provisioning/`(datasource·dashboard·alerting 등록), `monitoring/grafana/dashboards/mini-mes.json`(대시보드 정의)

### 알림 (Grafana Alerting → Discord)

`monitoring/grafana/provisioning/alerting/alerting.yml`로 알림 규칙과 수신처가 자동 등록됩니다 (Grafana "Alerting > Alert rules"의 `mini-mes` 폴더).

| 규칙 | 조건 | 심각도 |
|---|---|---|
| 열화 경보 | 설비별 `mes_drift_alarm == 1`이 30초 지속 | warning |
| 급변 이상 다발 | 최근 10분 판정 중 이상 비율 > 20% (판정 5건 이상일 때만). 건수가 아닌 비율이라 시뮬레이터 기본 이상 비율(5%)에서는 울리지 않음 | warning |
| 수집 대상 다운 | api·워커 `up == 0`이 1분 지속 | critical |
| 이상탐지 모델 없음 | `mes_anomaly_model_loaded == 0`이 5분 지속 | warning |

- 수신처: Discord 웹훅. URL은 `.env`의 `DISCORD_WEBHOOK_URL`(Helm은 `<release>-alerting` Secret, `infra/k3s/deploy.sh`가 `.env`에서 만듦)으로만 받습니다. 알림은 `알림 이름 + 설비`로 묶고 30초 대기 후 발송, 계속되면 4시간마다 재발송, 해소되면 해소 알림.
- URL이 없으면 Grafana가 시작하지 못하므로(`could not find webhook url`) 연결되지 않는 예약 도메인(`.invalid`)을 기본값으로 둡니다. 규칙은 평가·표시되고 발송만 실패합니다.
- 메시지 형식(알림 템플릿 `mini-mes`): 제목 `🔴 급변 이상 다발 · EQ-003`(긴급이면 `[긴급]`, 해소되면 `✅ 해소 · ...`), 본문은 요약과 조회 경로 두 줄. Grafana 기본 템플릿의 라벨 목록·localhost 링크는 뺌 (휴대폰 알림에서 읽기 쉽게)
- 알림 문구의 템플릿은 `{{ $labels.equipment_id }}`처럼 `$` 하나로 씁니다 (Grafana 11.3은 없는 환경변수 이름은 치환하지 않고, `$$`는 그대로 남김 — 확인함).
- 확인 (2026-09-29, compose): 이상 비율 50%·열화 모드 실행에서 "열화 경보"·"급변 이상 다발"이 설비 3대 모두 firing, 문구에 설비 ID·비율(61~68%) 표시. k3d(차트 0.7.0)에서 Secret 없이 규칙 4개 등록, Secret을 만들면 URL이 교체되는 것 확인. 실제 Discord 채널로 설비별 `[FIRING:1] 열화 경보 EQ-001 (mini-mes warning)` 메시지 수신 확인
- 알림 메시지의 Source·Silence 링크는 Grafana 외부 주소(`GF_SERVER_ROOT_URL`) 기본값인 `http://localhost:3000`을 가리킵니다. 서버에서는 접속 방식(SSH 터널 포트)에 맞춰 지정해야 합니다 (미처리)

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
  - Prometheus가 api·워커 Service를 스크레이프 (워커를 끄면 대상에서도 빠짐). 저장소는 PVC(`<release>-prometheus-data`, 기본 1Gi, `Recreate` 전략)라 파드를 다시 만들어도 메트릭·열화 경보 이력이 유지됩니다. `monitoring.prometheus.persistence.enabled=false`면 emptyDir(파드 삭제 시 이력 소실).
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

# 7) 자연어 질의 키 (선택) - 키는 values가 아니라 Secret으로만. 만든 뒤 api 파드를 재시작해야 읽음
kubectl create secret generic mini-mes-llm --from-env-file=.env
kubectl rollout restart deploy/mini-mes-api

# 8) 정리
helm uninstall mini-mes
k3d cluster delete mini-mes
```

minikube를 쓴다면 2)의 `k3d image import` 대신 `minikube image load mini-mes-api:latest mini-mes-anomaly-worker:latest`를 사용하면 됩니다.

**확인된 동작:** 차트 0.3.0까지는 api가 시작할 때 DB에 연결하는데 DB 파드가 아직 준비되지 않아 api 파드가 3회 재시작됐습니다. 0.3.1에서 `wait-for-db` initContainer를 추가한 뒤에는 initContainer가 `no response` 동안 기다렸다가 `accepting connections` 이후 api를 시작해 **RESTARTS 0**, api 로그의 DB 연결 오류 0건을 k3d에서 확인했습니다. anomaly-worker는 DB 연결 실패를 로그로 남기고 재시도하므로 원래부터 재시작 없이 정상화됩니다.

**k3d 검증 결과** (2026-09-28, k3d v5.7.4 / k3s v1.30.4 / Helm v3.16.2, 차트 0.3.0)

- 워커: 설치 직후 `모델 없음` 대기 → `kubectl exec`로 정상 데이터 학습 → 이상 섞인 데이터 판정·기록 → 워커 파드를 지워도 새 파드가 PVC에서 모델을 다시 읽고 이미 판정한 건은 다시 기록하지 않음
- 모니터링: 파드 5개(db·api·워커·Prometheus·Grafana) Running, 스크레이프 대상 api·워커 모두 up, Grafana에 대시보드(패널 9개) 로드·조회 성공
- Prometheus 영속성(차트 0.3.2): PVC 모드에서 파드를 지우고 새 파드가 떠도 가장 오래된 샘플 시각이 그대로(06:45:12, 새 파드 시작 06:47:02, WAL 재생 정상). 대조로 emptyDir 모드에서는 새 파드 시작 이후 샘플만 남음
- 예약 리포트(차트 0.6.0, 2026-09-29, k3s v1.36.4): CronJob `mini-mes-daily-report`(`10 0 * * *`, `timeZone: Asia/Seoul`) 등록, `kubectl create job --from=cronjob/...`로 수동 실행 → Job 완료, 전날(9/28) 설비 3대 행이 `daily_report`에 저장 (빈 DB라 수치 0). 정해진 시각의 자동 실행과 API가 실패했을 때 Job 실패 처리는 아직 확인하지 않음
- 정적 검증: `helm lint` 통과, 렌더링된 리소스 20개 kubeconform(strict) 통과 (차트 0.6.0 기준, CronJob 포함. Prometheus PVC 끄면 19개, `monitoring.enabled=false`면 11개)
- 자연어 질의 키(차트 0.4.0): api 컨테이너가 `<release>-llm` Secret(`nlq.existingSecret`로 변경 가능)의 `ANTHROPIC_API_KEY`·`GEMINI_API_KEY`를 `optional` 참조 — Secret이 없으면 api는 정상 동작하고 `/query`만 503. 제공자·모델은 `nlq.provider`, `nlq.claudeModel`, `nlq.geminiModel`. k3d 확인: Secret 없이 파드 5개 재시작 0회·`/health` 200·`/query` 503 → `.env`로 Secret 생성·api 재시작 후 파드에 `GEMINI_API_KEY` 주입(값은 출력하지 않고 길이만 확인), `/query`가 Gemini까지 도달(당일 무료 한도 소진으로 429 응답 — 답변 생성까지는 미확인)

## Oracle Cloud K3s 배포 (Terraform, 진행 중)

K3s 서버는 Oracle Cloud 상시 무료 ARM VM(A1.Flex, 오사카 `ap-osaka-1`)에 올립니다. `infra/terraform/oci/`가 VCN·인터넷 게이트웨이·서브넷·보안 목록·VM을 만들고, cloud-init이 K3s를 설치합니다.
구성도·보안 설계·CI/CD·검증 기록은 **[인프라 상세 문서](docs/infra.md)**([GitHub Pages](https://pmhllll12.github.io/mini-mes/infra/))에 정리했습니다.

- **외부 노출 최소화:** 보안 목록 인바운드는 SSH(22)를 내 IP(`allowed_ssh_cidr`, `0.0.0.0/0`은 validation으로 거부)에만 허용합니다. K3s API·api·Grafana는 열지 않고 SSH 터널로 접속합니다. `/query`를 공개하면 LLM 한도가 남용될 수 있어 공개 여부는 따로 정합니다.
- **Oracle Ubuntu 이미지의 iptables:** 기본 규칙이 22 외 INPUT과 모든 FORWARD를 REJECT해 파드 네트워크가 막히므로, cloud-init에서 REJECT 규칙만 지웁니다 (외부 방화벽은 보안 목록이 담당).
- **이미지:** VM이 ARM이라 CI가 main 푸시 때 `ghcr.io/pmhllll12/mini-mes-{api,anomaly-worker}`를 amd64/arm64로 빌드해 올립니다 (`sha-<커밋>`, `latest` 태그). 서버용 values는 `infra/k3s/values-oci.yaml`.
- **자격 증명:** OCI API 키는 `~/.oci/config`에서 읽고, `terraform.tfvars`·state·kubeconfig는 gitignore.

```bash
cd infra/terraform/oci
cp terraform.tfvars.example terraform.tfvars   # tenancy_ocid, allowed_ssh_cidr(내 IP/32) 입력
terraform init
terraform plan -out=tfplan && terraform apply tfplan
# 무료 ARM 재고 부족("Out of host capacity")이면 5분 간격 재시도 (다른 오류는 즉시 중단)
TF_VAR_ocpus=1 TF_VAR_memory_gb=6 ./retry-apply.sh

# 배포: 터널을 켜 둔 채 (terraform output kube_tunnel)
ssh -N -L 16443:127.0.0.1:6443 ubuntu@$(terraform output -raw public_ip)
SERVER_IP=$(terraform output -raw public_ip) GRAFANA_ADMIN_PASSWORD=... ../../k3s/deploy.sh
# 로컬 검증: KUBE_CONTEXT=k3d-<클러스터> GRAFANA_ADMIN_PASSWORD=... infra/k3s/deploy.sh <이미지 태그>
```

**배포 경로 로컬 검증 (2026-09-29, k3d + k3s v1.36.4, 서버와 같은 버전):** `KUBE_CONTEXT=k3d-mini-mes-oci GRAFANA_ADMIN_PASSWORD=... infra/k3s/deploy.sh sha-f33e75c` (`KUBE_CONTEXT`를 주면 SSH로 kubeconfig를 가져오지 않고 그 컨텍스트에 같은 values로 배포)
- GHCR에서 `sha-f33e75c` 이미지를 인증 없이 받아 파드 5개 Running, 재시작 0회
- `.env`로 만든 Secret에서 `GEMINI_API_KEY` 주입(값은 출력하지 않고 길이만 확인), `GEMINI_MODEL=gemini-2.5-flash`
- `/health` 200, 정상 데이터 학습(설비 3개) → 이상 섞인 100건×3 판정(이상 60건), Prometheus 스크레이프 대상 api·워커 up
- Grafana: 기본 비밀번호 `admin/admin`은 401, 넘긴 비밀번호로만 관리자 API 200
- 로컬 PC는 amd64라 arm64 이미지의 실행은 확인하지 못함 (CI에서 arm64 빌드·의존성 설치까지만 확인)

**현재 상태 (2026-09-29):** `plan` 6개 중 네트워크 5개 생성, VM은 오사카 A1 재고 부족(`500-InternalError, Out of host capacity`)으로 실패해 재시도 대기 중. 서버 배포 결과는 VM 생성 후 기록합니다.

## 기술 스택

- 현재: Python, FastAPI, SQLAlchemy, PostgreSQL 16, Docker Compose, Prometheus, Grafana, GitHub Actions(GHCR 멀티아키텍처 이미지), Helm/k3d(로컬 검증), scikit-learn(Isolation Forest), Claude·Gemini API(function calling), Terraform(OCI)
- 예정: Oracle Cloud K3s 서버 배포 (VM 생성 대기)

## 로드맵

| 주차 | 내용 | 상태 |
|---|---|---|
| 1주 | 스키마 설계, FastAPI 수집/조회 API, 시뮬레이터, 다중 설비 조회·CSV export | ✅ |
| 2주 | `quality_event`에 `production_log_id`(nullable FK) 추가, 설비별·불량유형별 불량 집계 API(`/quality/defect-summary`), 시뮬레이터가 불량 발생 시 연결된 품질 이벤트도 함께 전송, 설비 status 자동 갱신 | ✅ |
| 3주 | Prometheus + Grafana 모니터링 스택 추가 (`/metrics`, 대시보드 프로비저닝) | ✅ |
| 4주 | Helm 차트 작성 + k3d 로컬 검증 (Secret/PVC/probe, 서버 배포 대상은 미정). 5주차 이후 anomaly-worker(Deployment·모델 PVC·메트릭 Service), Prometheus·Grafana도 차트에 추가 | ✅ |
| 5주 | 이상탐지(예지보전) 워커: 설비별 Isolation Forest + robust z-score(정상 데이터만 학습), `/anomalies` API, 급변·열화 경보 분리, 워커 메트릭·Grafana 패널, 시뮬레이터 라벨 기반 성능 평가(가상 데이터 기준 급변 F1 v1 0.525 → v2 0.959), 워커 단위 테스트·CI | ✅ |
| 6주 | 자연어 질의 API(`POST /query`, Claude·Gemini function calling, 읽기 전용 도구 4개, 근거 반환), 평가 스크립트 — Gemini 8/12 평가 완료(8/8 통과), Claude 미평가 | 진행 중 |
| 7주 | Terraform(Oracle Cloud: VCN·보안 목록·A1 VM + cloud-init K3s), CI에서 amd64/arm64 이미지를 GHCR에 푸시·Terraform 검사, 문서화·데모 영상 — VM은 재고 부족으로 생성 대기 | 진행 중 |

## 알려진 한계

- 데이터는 시뮬레이터가 만든 가상 데이터이며 실제 설비 데이터가 아닙니다.
- `db/schema.sql`은 DB 최초 생성 시 한 번만 적용됩니다. 새 테이블 추가처럼 모든 문장이 `IF NOT EXISTS`인 변경은 `docker compose exec -T db psql -U mes_user -d mini_mes < db/schema.sql`로 기존 DB에 적용할 수 있고, 컬럼 변경 등은 `docker compose down -v` 후 다시 띄워야 합니다 (모델 볼륨도 지워져 재학습 필요). 마이그레이션 도구는 쓰지 않습니다.
- 이상탐지 관련 (상세는 [이상탐지 문서](docs/anomaly-detection.md#알려진-한계))
  - 성능 수치는 시뮬레이터가 만든 가상 데이터 기준이며, 시뮬레이터의 이상 패턴은 실제 고장 양상보다 단순합니다. 급변 recall 1.000은 쉬운 이상이라 나온 수치입니다.
  - 특징이 단순합니다(생산실적 1건 단위 3개 + 열화용 이동 구간 2개, 센서 데이터 없음). 점진적 열화 초반(진행도 25% 미만)은 거의 잡지 못합니다.
  - 설비별 모델이라 새 설비를 추가하거나 공정 조건이 바뀌면 재학습이 필요하고(자동 재학습 없음), 학습 데이터가 정상인지는 사람이 학습 구간을 지정해서 보장합니다.
  - 열화 경보는 열화가 끝난 뒤에도 몇 구간 더 켜져 있습니다. 경보 시작/해제 이력은 `drift_alarm`에 남지만 판정 건별 열화 점수는 메트릭으로만 남습니다.
  - `anomaly_result`에 생산실적 ID가 없어 `(equipment_id, ts)`로 같은 로그인지 판단합니다.
- 자연어 질의: 평가는 질문 12개 중 8개만 완료(Gemini 무료 한도), Claude는 미평가입니다. 수치를 단위(건/개)까지 정확히 전하는지는 자동 채점이 확인하지 못합니다. Helm 차트에서는 키를 담은 Secret(`<release>-llm`)을 직접 만들어야 `/query`가 동작합니다.
- Helm 차트(워커·Prometheus·Grafana 포함)는 k3d 로컬 검증까지만 했습니다 (k3d 기본 local-path 저장소라 PVC도 노드 한 대의 디스크에 있음).

## 개발 기간

2026.09 ~ 진행 중