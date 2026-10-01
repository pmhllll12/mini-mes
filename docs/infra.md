---
layout: page
title: 인프라
permalink: /infra/
---

같은 애플리케이션을 **Docker Compose → k3d(로컬 K3s) → Oracle Cloud K3s 서버** 순서로 옮기며 인프라를 단계적으로 쌓고 있습니다.
이 페이지는 배포 구성, 보안 설계, CI/CD, 검증 기록을 정리합니다.

> **현재 상태 (2026-10-01):** Terraform으로 Oracle Cloud 오사카 리전에 네트워크를 만들었고, 서버 VM은 무료 ARM 재고 부족("Out of host capacity")으로 생성을 기다리는 중입니다. 서버 배포 경로는 같은 K3s 버전의 k3d 클러스터에서 먼저 검증했습니다.

## 실행 환경

| | Docker Compose | k3d (로컬 K3s) | Oracle Cloud K3s |
|---|---|---|---|
| 용도 | 개발·평가 | Helm 차트 검증 | 서버 배포 (데모) |
| 이미지 | 로컬 빌드 | 로컬 빌드를 `k3d image import` | GHCR (CI가 amd64/arm64로 빌드) |
| 설정 | `docker-compose.yml` | 차트 기본값 | 차트 + `infra/k3s/values-oci.yaml` |
| 예약 리포트 | 직접 `POST /reports/daily` | CronJob | CronJob |
| 상태 | 사용 중 | 검증 완료 | 네트워크 생성, VM 대기 |

## 서버 구성

```
GitHub push(main) ─→ GitHub Actions ─ build amd64+arm64 ─→ GHCR
                                                             │ pull
PC ─ ssh :22 (my IP/32 only) ─→ OCI ap-osaka-1               │
 │                               └ VCN 10.0.0.0/16           │
 │                                  └ VM A1.Flex (ARM) ←─────┘
 │                                     └ K3s
 │                                        ├ api, db (PVC)
 └─ ssh -L 16443:127.0.0.1:6443 ────────→ ├ anomaly-worker (PVC)
    (kubectl, helm)                       ├ Prometheus (PVC), Grafana
                                          └ CronJob daily-report
```

- 외부에서 들어오는 경로는 내 IP의 SSH 하나뿐입니다. `kubectl`·`helm`은 SSH 터널로 K3s API에 붙습니다.
- 서버는 이미지를 GHCR에서 받아 옵니다 (빌드는 CI만).

### Terraform (`infra/terraform/oci/`)

| 자원 | 내용 |
|---|---|
| VCN · 인터넷 게이트웨이 · 라우트 테이블 | 10.0.0.0/16, 기본 경로를 인터넷 게이트웨이로 |
| 보안 목록 | 인바운드는 **SSH(22)를 `allowed_ssh_cidr`에만**, 그리고 Path MTU용 ICMP 3/4. 아웃바운드 전체 허용 |
| 서브넷 | 10.0.1.0/24, 공인 IP 할당 |
| VM | `VM.Standard.A1.Flex`, 기본 2 OCPU / 12GB, 부트 볼륨 50GB, 최신 Ubuntu 24.04(aarch64) 이미지 |
| cloud-init | iptables REJECT 규칙 제거 → K3s(v1.36.4) 설치 → kubeconfig를 ubuntu 사용자에게 복사 |
| 예산 (`budget.tf`) | 월 예산 1(청구 통화), 실제 지출이 1%를 넘으면 / 월말 예상이 예산을 넘으면 이메일. 상시 무료만 쓰므로 지출은 0이어야 하고, 과금이 생기면 바로 알기 위한 안전장치 (지출을 막지는 않음) |

- **상시 무료 범위 안에서만** 만듭니다 (A1 합계 4 OCPU / 24GB, 블록 볼륨 합계 200GB). 무료 체험 기간이 끝나도 사라지거나 과금되는 자원이 없게 하기 위함입니다.
- 이미지가 새로 나와도 VM을 다시 만들지 않도록 이미지 ID와 metadata는 `ignore_changes`로 둡니다.
- 무료 ARM 재고가 없으면 `retry-apply.sh`가 5분 간격으로 다시 시도합니다. 재고 부족이 아닌 오류는 즉시 멈춰, 설정 오류를 반복 호출하지 않습니다.
- `apply`는 `plan -out=tfplan`으로 저장한 plan만 적용합니다 (확인한 plan과 다른 변경이 들어가지 않게).

## 보안 설계

| 항목 | 방식 | 이유 |
|---|---|---|
| 외부 노출 | SSH 22만, 내 IP/32만. `0.0.0.0/0`은 변수 validation에서 거부 | 공격 표면 최소화 |
| K3s API | 6443을 열지 않고 SSH 터널(`ssh -L 16443:127.0.0.1:6443`)로 접속 | API 서버를 인터넷에 노출하지 않음. 공인 IP를 인증서(tls-san)에 넣을 필요도 없음 |
| api · Grafana · Prometheus | 공개하지 않고 SSH 터널 / `kubectl port-forward` | `/query`가 공개되면 LLM 무료 한도가 남용될 수 있음. 공개 여부는 인증을 붙인 뒤 결정 |
| OCI 자격 증명 | API 키는 `~/.oci/config`와 개인키 파일에만 | 코드·tfvars에 키를 두지 않음 |
| Terraform 파일 | `terraform.tfvars`, state, plan, kubeconfig는 gitignore | 테넌시 ID·IP·state의 자원 정보가 저장소에 올라가지 않게 |
| LLM 키 | `.env` → `kubectl create secret`, 차트는 `optional` 참조 | values(커밋됨)에 키를 넣지 않음. Secret이 없어도 `/query`만 503 |
| Grafana 관리자 비밀번호 | 배포 스크립트가 `GRAFANA_ADMIN_PASSWORD`로 전달 | 차트 기본값(admin)은 로컬 검증용 |
| Discord 웹훅 URL | `.env` → `<release>-alerting` Secret(optional), 없으면 `.invalid` 기본값 | URL만 알면 누구나 채널에 글을 쓸 수 있는 비밀값. 없어도 Grafana는 떠야 함 |

- Oracle의 Ubuntu 이미지는 기본 iptables가 22번 외 INPUT과 **모든 FORWARD를 REJECT**해 파드 네트워크가 막힙니다. cloud-init에서 REJECT 규칙만 지우고, 외부 방화벽 역할은 VCN 보안 목록에 맡깁니다.
- Grafana는 익명 Viewer 접근이 켜져 있습니다. 서버에서는 터널로만 열리므로 그대로 두되, 외부에 공개할 때는 꺼야 합니다.

## CI/CD

`.github/workflows/ci.yml`이 push·PR마다 실행됩니다.

```
 test (ruff, API pytest + Postgres)          ─┐
 anomaly-worker-test (ruff, 워커·평가 단위)    ├─→ docker-build (api, anomaly-worker)
 chart (복사본 diff, helm lint/template)      ─┘     amd64 + arm64 (QEMU, GHA 캐시)
 terraform (fmt -check, init -backend=false, validate)     main push일 때만 GHCR push
                                                             태그: sha-<커밋>, latest
```

- 서버 VM이 ARM(A1)이라 이미지를 **amd64/arm64 멀티아키텍처**로 빌드합니다. PR에서는 빌드만 확인하고, main에 push할 때만 GHCR에 올립니다.
- Terraform은 CI에서 자격 증명 없이 문법·구성만 검사하고, `plan`/`apply`는 로컬에서 합니다.
- **자동 배포(CD)는 아직 없습니다.** 서버 SSH가 내 IP에만 열려 있어 GitHub Actions 러너가 접속할 수 없기 때문입니다. 배포는 `infra/k3s/deploy.sh <이미지 태그>`로 커밋 단위 태그를 지정해 합니다. 서버 안에서 이미지를 끌어오는 방식(pull 기반 GitOps)이 다음 후보입니다.

## Helm 차트 (`charts/mini-mes/`, 0.7.0)

| 구성 요소 | 리소스 | 비고 |
|---|---|---|
| api | Deployment · Service | readiness/liveness probe, DB 준비를 기다리는 initContainer(`pg_isready`), LLM 키 Secret optional 참조 |
| db | Deployment · Service · PVC · Secret · ConfigMap | `db/schema.sql` 복사본을 최초 초기화 때 실행 |
| anomaly-worker | Deployment · Service(메트릭) · PVC(모델) | 모델은 PVC에 저장, 파드를 다시 만들어도 유지 |
| Prometheus · Grafana | Deployment · Service · PVC(Prometheus) · ConfigMap | compose의 `monitoring/` 구성과 같음, 대시보드 프로비저닝 |
| 일일 리포트 | CronJob | 매일 00:10 `Asia/Seoul`에 `POST /reports/daily` (전날 리포트 저장) |
| 알림 | Grafana 알림 규칙 ConfigMap, 웹훅 Secret(optional) | 열화 경보·급변 이상 다발·수집 대상 다운·모델 없음 → Discord |

- `files/schema.sql`, `files/grafana-dashboard.json`은 원본의 복사본입니다 (Helm은 차트 밖 파일을 못 읽음). CI의 chart 작업이 원본과 diff로 비교해 어긋나면 실패합니다.
- 차트가 쓰는 외부 이미지(postgres, Prometheus, Grafana, curl)는 모두 arm64를 지원합니다.

## 검증 기록

| 날짜 | 환경 | 내용 | 결과 |
|---|---|---|---|
| 2026-09-28 | k3d (k3s v1.30.4), 차트 0.3.x | 파드 5개, 워커 학습·판정, 모델 PVC 유지, Prometheus PVC 이력 유지, DB 대기 initContainer | 재시작 0회 (initContainer 추가 전 api 3회 재시작) |
| 2026-09-29 | k3d (k3s v1.36.4, 서버와 같은 버전) | `deploy.sh`로 **GHCR `sha-f33e75c` 이미지** 배포 (서버와 같은 values) | 파드 5개 Running·재시작 0회, LLM Secret 주입, 학습·판정, 스크레이프 대상 up, Grafana 기본 비밀번호 거부 |
| 2026-09-29 | k3d (k3s v1.36.4), 차트 0.6.0 | 일일 리포트 CronJob을 수동 Job으로 실행 | 전날 리포트 3행 저장, 렌더링 리소스 20개 kubeconform(strict) 통과 |
| 2026-09-29 | k3d (k3s v1.36.4), 차트 0.7.0 | Grafana 알림 프로비저닝 | 웹훅 Secret 없이 파드 5개 Running·규칙 4개 등록, Secret 생성 후 URL 교체 |
| 2026-09-29 | Oracle Cloud ap-osaka-1 | 예산·알림 규칙 (`plan -target`으로 예산 3개만, 재시도 스크립트 멈춘 뒤 적용) | 3개 생성 |
| 2026-09-29 | Oracle Cloud ap-osaka-1 | `terraform apply` (plan 6개) | 네트워크 5개 생성, VM은 `Out of host capacity` → 재시도 중 |
| 2026-10-01 | Oracle Cloud ap-osaka-1 | `retry-apply.sh` (1 OCPU / 6GB, 2분 간격), plan은 VM 1개 추가만 남음 | 계속 `Out of host capacity` → 재시도 중 |

**아직 확인하지 못한 것**

- arm64 이미지의 실제 실행 (로컬 PC가 amd64. CI에서 arm64 빌드·의존성 설치까지만 확인)
- 서버에서의 cloud-init(iptables 정리, K3s 설치)과 파드 네트워크
- CronJob이 정해진 시각에 자동 실행되는지, API가 실패할 때 Job이 실패로 처리되는지

## 발견한 문제

| 문제 | 원인 | 조치 |
|---|---|---|
| 차트 0.3.0에서 api 파드 3회 재시작 | api가 시작할 때 DB 파드가 아직 준비 전 | DB 대기 initContainer 추가 → 재시작 0회 |
| 서버가 이미지를 받을 곳이 없음 | 차트 기본값이 로컬 이미지(`mini-mes-api:latest`) | CI가 GHCR에 올리고, 서버용 values에서 GHCR 이미지 지정 |
| 서버(ARM)에서 amd64 이미지 실행 불가 | Oracle 상시 무료 VM은 ARM(A1) | buildx + QEMU로 amd64/arm64 멀티아키텍처 빌드 |
| Oracle Ubuntu 이미지에서 파드 네트워크 차단 (예상) | 기본 iptables의 FORWARD REJECT | cloud-init에서 REJECT 규칙 제거 (서버에서 확인 예정) |
| VM 생성 실패 `500-InternalError, Out of host capacity` | 오사카 리전 무료 ARM 재고 부족 (오사카는 가용 영역 1개) | 5분 간격 재시도 스크립트, 사양을 1 OCPU / 6GB로 낮춰 시도 |
| 웹훅 URL이 없으면 Grafana가 시작하지 못함 | Discord 수신처는 URL 필수 (`could not find webhook url property`) | 연결되지 않는 예약 도메인(`.invalid`)을 기본값으로, 실제 URL은 뒤에 붙는 Secret이 덮어씀 (`envFrom`은 뒤의 값 우선) |
| Grafana 기본 비밀번호로 로그인되는 것처럼 보임 | 익명 Viewer 접근이 켜져 있어 비밀번호가 틀려도 조회 API가 200 | 관리자 API(`/api/admin/settings`)로 다시 확인 → 기본 비밀번호는 401, 설정한 비밀번호만 200 |

## 한계와 다음 단계

- **스키마 마이그레이션 도구가 없습니다.** DB 초기화 스크립트는 최초 1회만 실행되므로, 서버에 한 번 배포한 뒤 테이블이 추가되면 `schema.sql`을 직접 적용해야 합니다 (현재 스키마는 모두 `IF NOT EXISTS`라 다시 적용해도 안전).
- 알림 메시지의 Grafana 링크가 `http://localhost:3000`(외부 주소 기본값)을 가리킵니다. 서버에서는 `GF_SERVER_ROOT_URL`을 접속 방식에 맞춰 지정해야 합니다.
- 노드 1대라 VM이 멈추면 서비스도 멈춥니다. PVC는 K3s 기본 local-path(노드 디스크)입니다.
- 다음: VM 생성 → 서버 배포와 위 미확인 항목 검증 → 자동 배포(pull 기반) 검토 → 외부 공개가 필요하면 인증을 붙인 Ingress.
