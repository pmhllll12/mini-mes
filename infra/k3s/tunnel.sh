#!/usr/bin/env bash
# 서버 K3s 안에 cloudflared를 띄워 https://<HOST>를 Cloudflare Tunnel로 공개한다.
# 보안 목록은 SSH만 열어 둔다 — cloudflared가 Cloudflare로 나가는 연결만 만들고, 클러스터 안에서 Traefik:443으로 전달.
#
# 준비: `cloudflared tunnel login`(~/.cloudflared/cert.pem), deploy.sh로 차트 배포, SSH 터널(16443)과 deploy.sh가 만든 kubeconfig
# 사용법: infra/k3s/tunnel.sh
#   HOST(기본 mes.pmhllll12.cloud), TUNNEL(기본 mini-mes-demo — 개인 PC 임시 데모와 같은 터널을 이어 쓴다)
#   다시 실행해도 됨 (터널·DNS 재사용, Secret·ConfigMap·Deployment는 apply)
# 터널 인증 정보(~/.cloudflared/<id>.json)는 파일로 서버에 복사하지 않고 kubectl로 Secret에만 넣는다.
set -euo pipefail

HOST="${HOST:-mes.pmhllll12.cloud}"
TUNNEL="${TUNNEL:-mini-mes-demo}"
CLOUDFLARED_VERSION=2026.9.3
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-$ROOT/infra/k3s/kubeconfig}"
CF_DIR="$HOME/.cloudflared"

[[ -f "$CF_DIR/cert.pem" ]] || { echo "$CF_DIR/cert.pem 없음 → cloudflared tunnel login 먼저"; exit 1; }
kubectl get nodes >/dev/null

tunnel_id() {
  cloudflared tunnel list --name "$TUNNEL" -o json |
    python3 -c 'import json, sys; t = json.load(sys.stdin); print(t[0]["id"] if t else "")'
}
ID="$(tunnel_id)"
if [[ -z "$ID" ]]; then
  cloudflared tunnel create "$TUNNEL" >/dev/null
  ID="$(tunnel_id)"
fi
echo "터널 $TUNNEL ($ID)"
cloudflared tunnel route dns "$TUNNEL" "$HOST"

kubectl create secret generic cloudflared-credentials --from-file=credentials.json="$CF_DIR/$ID.json" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: cloudflared
data:
  config.yml: |
    tunnel: $ID
    credentials-file: /etc/cloudflared/creds/credentials.json
    metrics: 0.0.0.0:2000
    no-autoupdate: true
    ingress:
      # Traefik 인증서는 자체 서명(selfsigned ClusterIssuer)이라 검증은 끄고, SNI는 실제 호스트로 보내 IngressRoute와 맞춘다
      - hostname: $HOST
        service: https://traefik.kube-system.svc.cluster.local:443
        originRequest:
          noTLSVerify: true
          originServerName: $HOST
      - service: http_status:404
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: cloudflared
spec:
  replicas: 1
  selector:
    matchLabels: { app: cloudflared }
  template:
    metadata:
      labels: { app: cloudflared }
      annotations:
        # 설정이 바뀌면 파드를 다시 만든다
        config-hash: "$(printf '%s' "$ID$HOST" | sha256sum | cut -c1-16)"
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 65532
      containers:
        - name: cloudflared
          image: cloudflare/cloudflared:$CLOUDFLARED_VERSION
          args: [tunnel, --config, /etc/cloudflared/config/config.yml, run]
          livenessProbe:
            httpGet: { path: /ready, port: 2000 }
            initialDelaySeconds: 10
            periodSeconds: 10
            failureThreshold: 3
          resources:
            requests: { cpu: 10m, memory: 32Mi }
            limits: { memory: 128Mi }
          securityContext:
            allowPrivilegeEscalation: false
            readOnlyRootFilesystem: true
            capabilities: { drop: [ALL] }
          volumeMounts:
            - { name: config, mountPath: /etc/cloudflared/config, readOnly: true }
            - { name: creds, mountPath: /etc/cloudflared/creds, readOnly: true }
      volumes:
        - name: config
          configMap: { name: cloudflared }
        - name: creds
          secret: { secretName: cloudflared-credentials }
EOF
kubectl rollout status deploy/cloudflared --timeout=120s
for _ in $(seq 30); do
  if kubectl logs deploy/cloudflared 2>&1 | grep -q "Registered tunnel connection"; then
    echo "연결됨 → https://$HOST/chat"
    exit 0
  fi
  sleep 2
done
kubectl logs --tail 20 deploy/cloudflared
echo "터널 연결을 확인하지 못함"
exit 1
