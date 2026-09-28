# mini-mes

제조 설비의 생산실적·가동률(OEE)·품질이력을 수집/집계하는 미니 MES.
목표: 인프라 설계·자동화 역량을 보여주는 취업용 개인 포트폴리오.

## 스택
FastAPI + SQLAlchemy + PostgreSQL, Docker Compose (이후 K3s/Helm, Terraform, GitHub Actions)

## 구조
- api/ : FastAPI 앱 (main.py, models.py, schemas.py, oee.py, quality.py, database.py)
- db/schema.sql : 테이블 정의 + 설비 시드 3개
- simulator/simulate.py : 가상 설비 데이터 전송기

## 로컬 실행 주의
- API 포트는 호스트 8001 (8000은 WSL의 다른 프로세스가 사용 중)
- 스키마 변경 시 `docker compose down -v` 후 재기동 (schema.sql은 최초 1회만 실행됨)

## 설계 원칙 (실무 경험 기반 개선점)
- 설비를 하나씩 조회하지 않고 다중/전체 설비를 한 번에 조회 (equipment_ids 파라미터)
- 화면 조작 없이 API로 조건 지정 + CSV 바로 내보내기
- 예정: 자연어 질의(Gemini function calling), 예약 리포트

## 로드맵
1주 코어 API/시뮬레이터 → 2주 OEE 고도화 → 3주 컨테이너 검증 → 4주 K3s/Helm
→ 5주 이상탐지(Isolation Forest) → 6주 자연어 질의 → 7주 Terraform/CI-CD/문서화

## 알려진 이슈
- (해결됨, 2주차) production_log.qty_defect 와 quality_event 가 서로 연결되어 있지 않던 문제 → quality_event.production_log_id(nullable FK) 추가, `/quality/defect-summary` API로 설비별·불량유형별 집계 제공
