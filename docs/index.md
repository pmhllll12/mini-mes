---
layout: page
title: 개요
permalink: /
---

제조 설비의 생산실적·가동률(OEE)·품질 이력을 수집하고 집계하는 미니 MES(Manufacturing Execution System) 개인 프로젝트입니다.
Docker Compose → K3s/Helm → Terraform → CI/CD 순으로 인프라를 단계적으로 고도화하며 만들고 있습니다.

- 소스 코드: [github.com/pmhllll12/mini-mes](https://github.com/pmhllll12/mini-mes)
- 실행 방법·API 상세: [README](https://github.com/pmhllll12/mini-mes#readme)
- 이상탐지 설계와 성능 평가: [이상탐지]({{ '/anomaly-detection/' | relative_url }})
- 자연어 질의 설계와 평가: [자연어 질의]({{ '/nlq/' | relative_url }})
- 배포 구성·보안 설계·CI/CD·검증 기록: [인프라]({{ '/infra/' | relative_url }})

> **현재 상태:** 7주차 진행 중 — 핵심 API + 설비 시뮬레이터, 불량 이력 연결, Prometheus/Grafana 모니터링·Discord 알림, GitHub Actions CI, Helm 차트 + k3d 로컬 검증, 이상탐지 워커 + 가상 데이터 기준 성능 평가, 자연어 질의 API(Gemini 13/13 통과, Claude 미평가), 예약 리포트.
> **배포 상태:** Helm 차트는 k3d에서 검증을 마쳤고, 서버와 같은 K3s 버전·같은 values·GHCR 이미지로 서버 배포 경로까지 로컬에서 확인했습니다. Oracle Cloud 상시 무료 ARM VM(오사카)은 Terraform으로 네트워크까지 만들었고, VM은 무료 ARM 재고 부족으로 생성을 재시도하는 중입니다 (2026-10-01 기준).

## 배경

제조 현장에서 수율 데이터 관리와 가공 품질 관리를 담당하며, MES에서 데이터를 뽑는 일이 가장 불편했습니다.
설비마다 화면을 따로 열어 조건을 걸고, 다운로드한 뒤 엑셀에서 다시 합치고 가공해야 했습니다.
이 경험을 바탕으로, 그 데이터를 다루는 시스템을 직접 설계해 보는 프로젝트입니다.

## 실무 불편함 → 설계 반영

| 실무에서 겪은 불편함 | 이 프로젝트의 해결 | 상태 |
|---|---|---|
| 화면에서 기간·설비 조건을 매번 수동 설정 | API 파라미터로 조건 지정 | 구현 |
| 설비를 하나씩 따로 조회 | `equipment_ids` 다중 지정, 생략 시 전체 설비 일괄 조회 | 구현 |
| 다운로드 후 엑셀에서 재가공 | 조건에 맞는 CSV를 바로 내려받는 export API | 구현 |
| 사람이 매번 조작해야 해서 자동화 불가 | REST API 제공 | 구현 |
| 원하는 정보를 말로 묻고 싶음 | 자연어 질의 `POST /query` (LLM function calling, 읽기 전용 도구) | 구현 |
| 같은 리포트를 반복해서 수동 추출 | 예약 리포트: 매일 새벽 전날 설비별 요약을 DB에 스냅샷으로 저장(K8s CronJob), 기간·설비 지정 조회·CSV (메일 발송은 미구현) | 구현 |

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

## 화면

![Grafana 대시보드]({{ '/assets/images/grafana-dashboard.png' | relative_url }})

*Grafana "mini-mes 개요" 대시보드 (2026-09-30, 시뮬레이터 가상 데이터: 급변 이상 5%·점진적 열화가 섞인 5분 실행분). 위에서부터 OEE·가동률·양품률, 불량 집계, API 요청·지연시간, 이상 점수(점선 = 설비별 threshold)·열화 점수와 경보 상태, 자연어 질의 요청·도구 호출.*

## 주요 기능

| 영역 | 내용 |
|---|---|
| 수집 | `POST /production-logs`(생산실적), `POST /quality-events`(불량 유형·심각도, 생산실적 건과 연결) |
| 조회·집계 | `/oee`, `/quality/defect-summary`, `/anomalies`, `/drift-alarms` — 모두 `equipment_ids` 생략 시 전체 설비를 한 번에 조회 |
| 내보내기 | `/export/production-logs` — 조건(기간·설비)을 넘기면 바로 CSV |
| 자연어 질의 | `POST /query` — LLM(Claude·Gemini)이 읽기 전용 도구 6개를 골라 호출하고 답변과 근거(도구·인자·결과)를 반환. Gemini 13/13 통과 |
| 이상탐지 | 설비별 Isolation Forest 워커(별도 컨테이너), 정상 데이터만 학습, 모델은 볼륨에 저장 |
| 모니터링 | API·워커 `/metrics` → Prometheus → Grafana 대시보드 (프로비저닝 파일로 자동 구성) |
| CI | GitHub Actions: ruff, API 테스트(Postgres 서비스 컨테이너), 워커 단위 테스트, API·워커 이미지 빌드 |
| 배포 | Helm 차트(api·db·anomaly-worker·Prometheus·Grafana, Secret/PVC/probe) + k3d 로컬 검증 |

## 이상탐지 결과 요약

> **이 결과는 시뮬레이터가 만든 가상 데이터 기준이며 실제 설비 성능이 아니다.**

학습(정상 데이터만, 900건)과 평가(이상 비율 19.3%, 1500건)는 서로 다른 시뮬레이터 실행분입니다.

| 모델 | Precision | Recall | F1 |
|---|---|---|---|
| v2: Isolation Forest + robust z-score (현재) | 0.920 | 1.000 | 0.959 |
| v1: Isolation Forest 단독 (같은 평가 데이터로 재채점) | 0.812 | 0.388 | 0.525 |

점진적 열화에는 v2의 recall이 0.407로 낮고, 이동 구간 특징(C안, 옵션)을 켜면 0.612로 오르는 대신 급변 이상의 오탐이 늘어납니다. v1의 recall이 낮았던 원인(Isolation Forest의 점수 포화), 개선 과정, 설비별 결과와 주의사항은 [이상탐지 페이지]({{ '/anomaly-detection/' | relative_url }})에 정리했습니다.

## 기술 스택

- 현재: Python, FastAPI, SQLAlchemy, PostgreSQL 16, Docker Compose, Prometheus, Grafana, GitHub Actions, Helm/k3d(로컬 검증), scikit-learn(Isolation Forest)
- 예정: Oracle Cloud K3s 서버 배포 (Terraform 작성, VM 생성 대기)

## 로드맵

| 주차 | 내용 | 상태 |
|---|---|---|
| 1주 | 스키마 설계, FastAPI 수집/조회 API, 시뮬레이터, 다중 설비 조회·CSV export | ✅ |
| 2주 | 불량 이력과 생산실적 연결, 설비별·불량유형별 불량 집계 API, 설비 status 자동 갱신 | ✅ |
| 3주 | Prometheus + Grafana 모니터링 스택 | ✅ |
| 4주 | Helm 차트 + k3d 로컬 검증 | ✅ |
| 5주 | 이상탐지(예지보전) 워커, `/anomalies` API, 워커 메트릭·Grafana 패널, 라벨 기반 성능 평가, CI | ✅ |
| 6주 | 자연어 질의 API (Claude·Gemini function calling) — Gemini 13/13 통과, Claude 미평가 | ✅ |
| 7주 | Terraform(Oracle Cloud), GHCR 멀티아키텍처 이미지, 문서화·데모 영상 | 진행 중 |
