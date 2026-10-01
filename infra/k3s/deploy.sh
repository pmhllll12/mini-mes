#!/usr/bin/env bash
# Oracle Cloud K3s 서버에 Helm 차트 배포.
# 먼저 다른 터미널에서 SSH 터널을 켜 둔다 (terraform output kube_tunnel):
#   ssh -N -L 16443:127.0.0.1:6443 ubuntu@<public_ip>
# 사용법: SERVER_IP=<public_ip> GRAFANA_ADMIN_PASSWORD=... MES_ADMIN_PASSWORD=... infra/k3s/deploy.sh [이미지 태그, 기본 latest] [helm 추가 인자...]
#   MES_ADMIN_PASSWORD: 외부 공개 시 데이터 쓰기 요청(basic-auth, 사용자 MES_ADMIN_USER 기본 admin) 비밀번호
# 로컬 검증: SERVER_IP 대신 KUBE_CONTEXT=k3d-<클러스터>를 주면 그 컨텍스트에 같은 values로 배포 (kubeconfig를 가져오지 않음)
#   예: KUBE_CONTEXT=k3d-mini-mes ... deploy.sh latest --set ingress.host=mes.localtest.me --set ingress.clusterIssuer=selfsigned
set -euo pipefail

: "${GRAFANA_ADMIN_PASSWORD:?GRAFANA_ADMIN_PASSWORD 필요}"
: "${MES_ADMIN_PASSWORD:?MES_ADMIN_PASSWORD 필요 (데이터 쓰기 basic-auth)}"
CERT_MANAGER_VERSION=v1.21.2
TAG="${1:-latest}"
shift || true
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

if [[ -n "${KUBE_CONTEXT:-}" ]]; then
  KUBECTL=(kubectl --context "$KUBE_CONTEXT")
  HELM=(helm --kube-context "$KUBE_CONTEXT")
else
  : "${SERVER_IP:?SERVER_IP 필요 (terraform output public_ip), 로컬 검증이면 KUBE_CONTEXT}"
  export KUBECONFIG="$ROOT/infra/k3s/kubeconfig"
  # 서버의 kubeconfig를 가져와 터널 포트(16443)를 가리키게 바꾼다 (gitignore됨)
  ssh "ubuntu@$SERVER_IP" cat .kube/config | sed 's#https://127.0.0.1:6443#https://127.0.0.1:16443#' > "$KUBECONFIG"
  chmod 600 "$KUBECONFIG"
  KUBECTL=(kubectl)
  HELM=(helm)
fi
"${KUBECTL[@]}" get nodes

# LLM 키: 루트 .env가 있으면 Secret으로 (없으면 /query만 503)
if [[ -f "$ROOT/.env" ]]; then
  "${KUBECTL[@]}" create secret generic mini-mes-llm --from-env-file="$ROOT/.env" --dry-run=client -o yaml | "${KUBECTL[@]}" apply -f -
  # 알림 웹훅: .env에 값이 있을 때만 (없으면 Grafana 기본값 - 발송만 실패)
  WEBHOOK="$(grep -E '^DISCORD_WEBHOOK_URL=.+' "$ROOT/.env" | cut -d= -f2- || true)"
  if [[ -n "$WEBHOOK" ]]; then
    "${KUBECTL[@]}" create secret generic mini-mes-alerting --from-literal=DISCORD_WEBHOOK_URL="$WEBHOOK" \
      --dry-run=client -o yaml | "${KUBECTL[@]}" apply -f -
  fi
fi

# 외부 공개 준비: Traefik(클라이언트 IP 보존) → cert-manager → ClusterIssuer → basic-auth Secret
"${KUBECTL[@]}" apply -f "$ROOT/infra/k3s/traefik-config.yaml"
"${HELM[@]}" repo add jetstack https://charts.jetstack.io --force-update >/dev/null
"${HELM[@]}" upgrade --install cert-manager jetstack/cert-manager --version "$CERT_MANAGER_VERSION" \
  --namespace cert-manager --create-namespace --set crds.enabled=true --wait --timeout 5m
"${KUBECTL[@]}" apply -f "$ROOT/infra/k3s/cluster-issuers.yaml"
# htpasswd 형식(apr1). 비밀번호는 stdin으로 넘겨 프로세스 목록에 남지 않게
USERS="${MES_ADMIN_USER:-admin}:$(printf '%s' "$MES_ADMIN_PASSWORD" | openssl passwd -apr1 -stdin)"
"${KUBECTL[@]}" create secret generic mini-mes-basic-auth --from-literal=users="$USERS" \
  --dry-run=client -o yaml | "${KUBECTL[@]}" apply -f -

"${HELM[@]}" upgrade --install mini-mes "$ROOT/charts/mini-mes" \
  -f "$ROOT/infra/k3s/values-oci.yaml" \
  --set image.tag="$TAG" --set anomalyWorker.image.tag="$TAG" \
  --set monitoring.grafana.adminPassword="$GRAFANA_ADMIN_PASSWORD" \
  "$@" \
  --wait --timeout 10m

"${KUBECTL[@]}" get pods
