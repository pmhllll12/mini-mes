# mini-mes

제조 설비의 생산실적·가동률(OEE)·품질이력을 수집/집계하는 미니 MES.
목표: 인프라 설계·자동화 역량을 보여주는 취업용 개인 포트폴리오.

## 스택
FastAPI + SQLAlchemy + PostgreSQL, Docker Compose, Prometheus + Grafana, GitHub Actions, Helm(k3d 로컬 검증), scikit-learn(Isolation Forest) (이후 Terraform, K3s 서버 배포)

## 구조
- api/ : FastAPI 앱 (main.py, models.py, schemas.py, oee.py, quality.py, anomaly.py, metrics.py, database.py)
- api/tests/ : pytest 테스트 (Postgres 필요, db/schema.sql 적용된 DB 대상)
- anomaly-worker/ : 이상탐지 워커 컨테이너 (features.py 특징 추출, model.py 설비별 Isolation Forest, train.py 학습 CLI, worker.py 주기 추론 + :9100 메트릭, db.py). 모델은 anomaly_models 볼륨(/models)에 저장. tests/는 DB 없이 실행
- evaluate/evaluate.py : 시뮬레이터 라벨과 anomaly_result를 (equipment_id, ts)로 매칭해 precision/recall/F1 계산 (anomaly-worker 이미지 안에서 실행)
- db/schema.sql : 테이블 정의 + 설비 시드 3개
- simulator/simulate.py : 가상 설비 데이터 전송기. --labels-file로 이상 여부 라벨(JSONL, run_id 단위)을 남김. labels.jsonl은 gitignore
- monitoring/ : Prometheus 스크레이프 설정(api, anomaly-worker), Grafana 데이터소스·대시보드 프로비저닝
- .github/workflows/ci.yml : push/PR 시 lint(ruff)·API 테스트·워커/평가 단위 테스트·API/워커 Docker 빌드
- docs/ : GitHub Pages(Jekyll, minima 테마) 소개 사이트 (index.md 개요, anomaly-detection.md 평가 상세). README의 수치·로드맵을 바꾸면 함께 갱신
- charts/mini-mes/ : api·db Helm 차트 (k3d/minikube 로컬 검증용, 서버 배포 대상 미정). files/schema.sql은 db/schema.sql 복사본이므로 수동 동기화 필요

## 로컬 실행 주의
- API 포트는 호스트 8001 (8000은 WSL의 다른 프로세스가 사용 중)
- docs/ Jekyll 로컬 미리보기는 포트 4002 (`cd docs && jekyll serve --port 4002`, http://localhost:4002/mini-mes/). 4000·4001은 다른 프로젝트(super-sub.cloud, demo)의 jekyll serve가 사용 중
- 스키마 변경 시 `docker compose down -v` 후 재기동 (schema.sql은 최초 1회만 실행됨). down -v는 모델 볼륨(anomaly_models)도 지우므로 재학습 필요 (README "이상탐지 워커" 절차)
- 로컬 python은 3.14라 scikit-learn 고정 버전 설치가 안 됨 → 워커/평가 테스트는 python:3.12 컨테이너에서 실행

## 이상탐지 규칙
- 학습은 정상 데이터만 (시뮬레이터 --anomaly-rate 0 실행분, train.py --since로 구간 지정). 이상이 섞인 데이터로 학습하지 않는다
- 평가는 학습과 다른 시뮬레이터 실행분으로만. threshold를 평가 데이터로 튜닝하지 않는다
- 수치를 좋게 만들려고 시뮬레이터의 이상 데이터 생성 방식을 바꾸지 않는다
- anomaly_result에는 log_id가 없음 → ts에 production_log.ts를 그대로 넣고 (equipment_id, ts)로 중복 방지

## 설계 원칙 (실무 경험 기반 개선점)
- 설비를 하나씩 조회하지 않고 다중/전체 설비를 한 번에 조회 (equipment_ids 파라미터)
- 화면 조작 없이 API로 조건 지정 + CSV 바로 내보내기
- 예정: 자연어 질의(Gemini function calling), 예약 리포트

## 로드맵
1주 코어 API/시뮬레이터 → 2주 OEE 고도화 → 3주 모니터링 → 4주 K3s/Helm (완료)
→ 5주 이상탐지(Isolation Forest) (완료) → 6주 자연어 질의 → 7주 Terraform/CI-CD/문서화

## 알려진 이슈
- (해결됨, 2주차) production_log.qty_defect 와 quality_event 가 서로 연결되어 있지 않던 문제 → quality_event.production_log_id(nullable FK) 추가, `/quality/defect-summary` API로 설비별·불량유형별 집계 제공
- (미해결, 5주차) Isolation Forest 점수가 학습 범위 밖에서 포화되어 recall이 낮음 (가상 데이터 기준 전체 P 0.896 / R 0.453 / F1 0.602). 개선 후보: 범위 이탈 robust z-score 결합, 이동평균 특징
- Helm 차트에는 anomaly-worker가 아직 없음
