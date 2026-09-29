#!/usr/bin/env bash
# Oracle Cloud K3s 서버에 Helm 차트 배포.
# 먼저 다른 터미널에서 SSH 터널을 켜 둔다 (terraform output kube_tunnel):
#   ssh -N -L 16443:127.0.0.1:6443 ubuntu@<public_ip>
# 사용법: SERVER_IP=<public_ip> GRAFANA_ADMIN_PASSWORD=... infra/k3s/deploy.sh [이미지 태그, 기본 latest]
set -euo pipefail

: "${SERVER_IP:?SERVER_IP 필요 (terraform output public_ip)}"
: "${GRAFANA_ADMIN_PASSWORD:?GRAFANA_ADMIN_PASSWORD 필요}"
TAG="${1:-latest}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export KUBECONFIG="$ROOT/infra/k3s/kubeconfig"

# 서버의 kubeconfig를 가져와 터널 포트(16443)를 가리키게 바꾼다 (gitignore됨)
ssh "ubuntu@$SERVER_IP" cat .kube/config | sed 's#https://127.0.0.1:6443#https://127.0.0.1:16443#' > "$KUBECONFIG"
chmod 600 "$KUBECONFIG"
kubectl get nodes

# LLM 키: 루트 .env가 있으면 Secret으로 (없으면 /query만 503)
if [[ -f "$ROOT/.env" ]]; then
  kubectl create secret generic mini-mes-llm --from-env-file="$ROOT/.env" --dry-run=client -o yaml | kubectl apply -f -
fi

helm upgrade --install mini-mes "$ROOT/charts/mini-mes" \
  -f "$ROOT/infra/k3s/values-oci.yaml" \
  --set image.tag="$TAG" --set anomalyWorker.image.tag="$TAG" \
  --set monitoring.grafana.adminPassword="$GRAFANA_ADMIN_PASSWORD" \
  --wait --timeout 10m

kubectl get pods
