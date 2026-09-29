# mini-mes

제조 설비의 생산실적·가동률(OEE)·품질이력을 수집/집계하는 미니 MES.
목표: 인프라 설계·자동화 역량을 보여주는 취업용 개인 포트폴리오.

## 스택
FastAPI + SQLAlchemy + PostgreSQL, Docker Compose, Prometheus + Grafana, GitHub Actions, Helm(k3d 로컬 검증), scikit-learn(Isolation Forest), Terraform(Oracle Cloud)

## 구조
- api/ : FastAPI 앱 (main.py, models.py, schemas.py, oee.py, quality.py, anomaly.py, metrics.py, database.py, report.py 일일 리포트, nlq_tools.py 자연어 질의 읽기 전용 도구 6개(결과 필드명에 단위 표기), nlq_providers.py Claude·Gemini·OpenAI 호환(openai_compat, requests로 호출) 어댑터)
- api/tests/ : pytest 테스트 (Postgres 필요, db/schema.sql 적용된 DB 대상). conftest.py의 autouse fixture가 테스트 프로세스가 넣은 행(after_insert 추적)과 워커가 그 로그를 판정한 결과를 지우고 설비 status를 복원 (KEEP_TEST_DATA=1이면 유지)
- anomaly-worker/ : 이상탐지 워커 컨테이너 (features.py 특징 추출, model.py 설비별 Isolation Forest + robust z-score 결합(v2, v1 번들도 호환), features.py build_features(--rolling-window 이동 구간 특징, 기본 꺼짐), train.py 학습 CLI, worker.py 주기 추론 + :9100 메트릭 + 급변/열화 경보 분리, db.py). 모델은 anomaly_models 볼륨(/models)에 저장. tests/는 DB 없이 실행
- evaluate/nlq_eval.py + nlq_questions.json : 자연어 질의 평가(도구 선택·인자·근거·단위·설비 언급·언어·안내 문구, --only로 일부만, --out에 도구 결과까지 저장). 실제 LLM 호출이라 비용·무료 한도 소모 → 실행 전 사용자 확인
- evaluate/evaluate.py : 시뮬레이터 라벨과 anomaly_result를 (equipment_id, ts)로 매칭해 precision/recall/F1 계산 (anomaly-worker 이미지 안에서 실행). --score-with DIR이면 그 모델로 직접 채점(DB 기록 없음, 모델 비교용), 유형별 recall 출력
- db/schema.sql : 테이블 정의 + 설비 시드 3개
- simulator/simulate.py : 가상 설비 데이터 전송기. --labels-file로 이상 여부 라벨(JSONL, run_id 단위, anomaly_type spike/drift)을 남김. --drift-rate로 점진적 열화 모드(기본 0이면 기존과 동일). labels.jsonl은 gitignore
- monitoring/ : Prometheus 스크레이프 설정(api, anomaly-worker), Grafana 데이터소스·대시보드·알림(provisioning/alerting/alerting.yml: 규칙 4개 + Discord, URL은 DISCORD_WEBHOOK_URL, 없으면 .invalid 기본값) 프로비저닝. 알림 문구 템플릿은 $ 하나로 ($$는 그대로 남음)
- .github/workflows/ci.yml : push/PR 시 lint(ruff)·API 테스트·워커/평가 단위 테스트·차트(복사본 동기화 diff, helm lint/template)·Terraform fmt/validate·API/워커 amd64+arm64 빌드(main 푸시 때만 GHCR 푸시)
- docs/ : GitHub Pages(Jekyll, minima 테마) 소개 사이트 (index.md 개요, anomaly-detection.md 이상탐지 상세 기록: 평가 절차·모델별 비교·C안·경보 분리 전체 표, nlq.md 자연어 질의 설계·평가·발견한 문제, infra.md 배포 구성·보안 설계·CI/CD·검증 기록). README는 요약·실행법·현재 결과만 두고 상세는 docs로. 수치·로드맵을 바꾸면 README·docs 함께 갱신
- infra/terraform/oci/ : Oracle Cloud(오사카 ap-osaka-1, 상시 무료 A1 ARM) VCN·보안 목록(SSH만, 내 IP)·VM + cloud-init K3s. 인증은 ~/.oci/config, terraform.tfvars·state는 gitignore. retry-apply.sh는 A1 재고 부족 시 재시도. infra/k3s/ : 서버용 values-oci.yaml(GHCR 이미지)·deploy.sh(SSH 터널 16443 경유 helm)
- charts/mini-mes/ : api·db·anomaly-worker(모델 PVC, 메트릭 Service)·Prometheus·Grafana(monitoring.enabled) Helm 차트. LLM 키는 values에 넣지 않고 <release>-llm Secret을 optional 참조(nlq.existingSecret, kubectl create secret generic mini-mes-llm --from-env-file=.env) (k3d/minikube 로컬 검증, 서버 배포는 infra/k3s/values-oci.yaml로 덮어씀). files/schema.sql, files/grafana-dashboard.json, files/grafana-alerting.yml은 db/schema.sql, monitoring/grafana/dashboards/mini-mes.json, monitoring/grafana/provisioning/alerting/alerting.yml 복사본 → 원본 수정 시 같이 수정 (CI chart 작업이 diff로 검사)

## 로컬 실행 주의
- API 포트는 호스트 8001 (8000은 WSL의 다른 프로세스가 사용 중)
- docs/ Jekyll 로컬 미리보기는 포트 4002 (`cd docs && jekyll serve --port 4002`, http://localhost:4002/mini-mes/). 4000·4001은 다른 프로젝트(super-sub.cloud, demo)의 jekyll serve가 사용 중
- 스키마 변경 시 `docker compose down -v` 후 재기동 (schema.sql은 최초 1회만 실행됨). down -v는 모델 볼륨(anomaly_models)도 지우므로 재학습 필요 (README "이상탐지 워커" 절차)
- helm·k3d·terraform·kubectl은 ~/.local/bin에 설치됨 (helm v3.16.2, k3d v5.7.4, terraform 1.16.4, kubectl 1.36.5). k3d 검증 시 호스트 8001은 compose API가 쓰므로 port-forward는 18001 등 다른 포트 사용
- LLM API 키는 루트 .env에만 (ANTHROPIC_API_KEY, GEMINI_API_KEY, .env.example 참고). 키 값을 출력·커밋하지 않는다. Gemini 무료 등급은 모델당 하루 20회 한도 (gemini-flash-latest=gemini-3.8-flash, gemini-2.5-flash는 별도 한도). Anthropic 조직 크레딧 $0이라 Claude는 미검증
- Gemini function calling: 함수 결과는 role="user"로 전송 (role="tool"은 Developer API가 400)
- 로컬 LLM: WSL의 Ollama(~/.ollama-local/bin/ollama, 0.32.15, localhost:11434, RTX 3050 8GB). API 컨테이너는 host.docker.internal:11434/v1 (compose extra_hosts). .env에 OPENAI_COMPAT_BASE_URL/MODEL, 기본 NLQ_PROVIDER=gemini 유지하고 요청의 provider=openai_compat로 사용. qwen2.5:7b-instruct는 9/13(형식 오류·ID 추측·중국어), qwen3:4b는 도구 호출을 텍스트로 내서 실패. 로컬 평가는 한도 없음 → 확인 없이 돌려도 됨(Gemini·Claude 평가만 사전 확인)
- 로컬 python은 3.14라 scikit-learn 고정 버전 설치가 안 됨 → 워커/평가 테스트는 python:3.12 컨테이너에서 실행

## 이상탐지 규칙
- 학습은 정상 데이터만 (시뮬레이터 --anomaly-rate 0 실행분, train.py --since로 구간 지정). 이상이 섞인 데이터로 학습하지 않는다
- 평가는 학습과 다른 시뮬레이터 실행분으로만. threshold를 평가 데이터로 튜닝하지 않는다
- 수치를 좋게 만들려고 시뮬레이터의 이상 데이터 생성 방식을 바꾸지 않는다
- anomaly_result에는 log_id가 없음 → ts에 production_log.ts를 그대로 넣고 (equipment_id, ts)로 중복 방지

## 설계 원칙 (실무 경험 기반 개선점)
- 설비를 하나씩 조회하지 않고 다중/전체 설비를 한 번에 조회 (equipment_ids 파라미터)
- 화면 조작 없이 API로 조건 지정 + CSV 바로 내보내기
- 자연어 질의는 LLM이 SQL을 만들지 않고 검증된 읽기 전용 도구만 호출, 응답에 근거(도구·인자·결과) 포함
- 예약 리포트: api/report.py가 KST 하루 설비별 요약을 daily_report에 스냅샷(덮어쓰기), POST/GET /reports/daily, 차트 CronJob(curl, 00:10 Asia/Seoul). 메일 발송은 없음

## 로드맵
1주 코어 API/시뮬레이터 → 2주 OEE 고도화 → 3주 모니터링 → 4주 K3s/Helm (완료)
→ 5주 이상탐지(Isolation Forest) (완료) → 6주 자연어 질의 (완료: Gemini 12/12, Claude 미평가. 건수·수량 혼동은 get_defect_summary 결과 필드명을 품질이벤트_건수/불량수량_개로 바꿔 개선, 2회 확인) → 7주 Terraform/CI-CD/문서화 (진행 중: Terraform 작성·네트워크 생성, A1 VM 재고 대기)

## 알려진 이슈
- (해결됨, 2주차) production_log.qty_defect 와 quality_event 가 서로 연결되어 있지 않던 문제 → quality_event.production_log_id(nullable FK) 추가, `/quality/defect-summary` API로 설비별·불량유형별 집계 제공
- (해결됨, 5주차) Isolation Forest 점수가 학습 범위 밖에서 포화되어 recall이 낮던 문제(v1: P 0.896 / R 0.453 / F1 0.602) → robust z-score 결합(v2: 새 평가 실행분 기준 P 0.920 / R 1.000 / F1 0.959). 시뮬레이터 이상이 쉬운 이상이라 나온 수치.
- (5주차, 옵션으로 유지) C안 이동 구간 특징(K=5): 점진적 열화 recall 0.407 → 0.612, 대신 이상 직후 오탐 증가로 급변 F1 0.970 → 0.861. 워커 기본은 v2.
- (5주차) 급변·열화 경보 분리: 급변=/models(v2)→anomaly_result, 열화=/models/drift(C, K=5)→mes_drift_* 메트릭 + 경보 시작/해제는 drift_alarm 테이블(/drift-alarms, 워커 재시작 시 진행 중 경보로 상태 복원, 설비당 진행 중 1건 부분 유일 인덱스). 급변 판정 건은 열화 경보 판단 보류(None). 히스테리시스 2구간 연속 시작/3구간 연속 해제(DRIFT_RAISE_AFTER/DRIFT_CLEAR_AFTER). 검증 실행분 기준 경보 76→25, 에피소드당 1.00, 도중 해제 0. 남은 문제: 열화 종료 후 약 5구간 경보 지속, 열화 초반 미감지. 자연어 질의에는 열화 이력 도구 없음(평가 질문 drift_history가 "조회 불가 안내"를 채점하므로 평가 끝난 뒤 검토)
- Helm 차트(워커·Prometheus·Grafana 포함)는 k3d 로컬 검증까지만. 차트 Prometheus 저장소는 PVC(monitoring.prometheus.persistence, 기본 켬, Recreate, fsGroup 65534)라 파드 재생성 후에도 이력 유지. k3d 검증 시 port-forward는 18001(api)/19090(prometheus)/13000(grafana) 사용 (compose와 충돌 방지)
