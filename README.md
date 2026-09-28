# mini-mes

제조 현장의 생산실적·설비가동률·품질 이력을 수집·집계·시각화하는
미니 MES(Manufacturing Execution System)를 처음부터 설계하고,
클라우드 인프라 배포 파이프라인까지 직접 구축한 개인 프로젝트입니다.

## 배경

영풍전자·오스템임플란트·디에스테크노에서 근무하며 수율 데이터 관리와
가공 품질 관리를 직접 담당했던 경험을, 이제는 그 데이터를 다루는
시스템 자체를 설계·구현하는 입장에서 재현해보고자 시작했습니다.

팀 프로젝트 [SUPERSUB](https://github.com/pmhllll12/super-sub.cloud)에서
FastAPI + K3s 기반 인프라 운영에 참여한 경험을 바탕으로,
이번에는 인프라 설계부터 IaC(Terraform), CI/CD까지 전 과정을
혼자 설계·구현했습니다.

## 실무 경험에서 나온 개선점

실무에서 MES 데이터를 뽑을 때 겪었던 불편함을 이 프로젝트의 설계 기준으로 삼았습니다.

| 실무에서 겪은 불편함 | 이 프로젝트에서의 해결 |
|---|---|
| 화면에서 조건(기간·설비)을 매번 수동으로 설정 | API 파라미터로 조건 지정, 클릭 없이 즉시 조회 |
| 설비를 하나하나 따로 조회해야 함 | `equipment_ids` 다중 지정 또는 전체 설비 일괄 조회 |
| 다운로드 후 엑셀에서 재가공(필터링·피벗) | `/export/production-logs`로 원하는 조건의 CSV를 바로 생성 |
| 자동화 불가능(사람이 매번 조작) | REST API + 자연어 질의로 스크립트/챗봇에서 바로 호출 가능 |
| 반복적으로 같은 리포트를 매번 새로 뽑음 | (예정) 예약 리포트로 정해진 시간에 자동 발송 |

## 핵심 기능

- 설비 가동현황 실시간 조회 (가동/정지/점검)
- 생산실적 집계 및 가동률(OEE) 계산
- 품질 이벤트 이력 관리 (불량 유형/발생 시점 태깅)
- (5주차 예정) Isolation Forest 기반 이상탐지(예지보전)
- (6주차 예정) 자연어 질의 API (Gemini function calling)

## 로컬 실행 (1주차)

```bash
git clone <this-repo>
cd mini-mes
docker compose up --build
```

- API: http://localhost:8000/docs (FastAPI 자동 문서)
- 설비 시뮬레이터 실행:

```bash
cd simulator
pip install requests
python simulate.py --api-url http://localhost:8000 --interval 5
```

시뮬레이터가 5초마다 3개 설비의 생산실적을 API로 전송하고,
`--anomaly-rate`로 이상 데이터 발생 빈도를 조절할 수 있습니다
(5주차 이상탐지 모델 검증에 사용).

## 가동률(OEE) 조회 예시

```bash
curl "http://localhost:8000/equipment/EQ-001/oee?hours=1"
```

```json
{
  "equipment_id": "EQ-001",
  "period_start": "...",
  "period_end": "...",
  "availability": 0.94,
  "quality_rate": 0.91,
  "oee": 0.856,
  "total_qty": 240,
  "total_defect": 22
}
```

## 아키텍처

```
[설비 시뮬레이터] → [FastAPI 수집 API] → [PostgreSQL]
                                              ↓
                                    [FastAPI 조회/집계 API]
                                              ↓
                                    [Grafana 대시보드]
```

Docker Compose(로컬) → K3s/Helm → Terraform → GitHub Actions CI/CD 순으로
단계적으로 인프라를 고도화할 예정입니다.

## 기술 스택

- Backend: FastAPI, SQLAlchemy, PostgreSQL
- Infra: Docker, Kubernetes(K3s), Helm, Terraform
- CI/CD: GitHub Actions
- Monitoring: Prometheus, Grafana
- AI: scikit-learn(Isolation Forest), Gemini(function calling)

## 로드맵

| 주차 | 내용 | 상태 |
|---|---|---|
| 1주 | 스키마 설계, FastAPI 수집/조회 API, 시뮬레이터 | ✅ |
| 2주 | OEE 계산 로직 고도화, 대시보드용 집계 API | |
| 3주 | Docker Compose 전체 스택 검증 | |
| 4주 | K3s/Helm 배포 전환 | |
| 5주 | 이상탐지(예지보전) 워커 추가 | |
| 6주 | 자연어 질의 API 추가 | |
| 7주 | Terraform, CI/CD, 문서화/데모 영상 | |

## 개발 기간

2026.XX ~ 2026.XX
