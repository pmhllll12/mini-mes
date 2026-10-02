#!/usr/bin/env bash
# 임시 데모: 개인 PC의 k3d 클러스터를 Cloudflare Tunnel로 공개 (Oracle VM이 생기기 전까지).
# 인바운드 포트를 열지 않는다 — cloudflared 컨테이너가 Cloudflare로 나가는 연결만 만들고,
# k3d 네트워크 안에서 serverlb:443(Traefik)으로 전달한다. 공개 TLS는 Cloudflare 엣지가 맡는다.
#
# 준비
#   1. 도메인이 Cloudflare DNS에 있고(Active) `cloudflared tunnel login`으로 ~/.cloudflared/cert.pem을 받아 둠
#   2. k3d 클러스터와 배포 (호스트에는 127.0.0.1로만 바인딩):
#        k3d cluster create mini-mes-demo --image rancher/k3s:v1.36.4-k3s1 -p 127.0.0.1:28443:443@loadbalancer
#        KUBE_CONTEXT=k3d-mini-mes-demo GRAFANA_ADMIN_PASSWORD=... MES_ADMIN_PASSWORD=... \
#          infra/k3s/deploy.sh latest -f infra/local-demo/values-demo.yaml
# 사용법: infra/local-demo/tunnel.sh
#   다시 실행해도 됨 (터널·DNS 레코드는 재사용, 컨테이너만 새로 띄움)
#   HOST(기본 mes.pmhllll12.cloud), TUNNEL(기본 mini-mes-demo), CLUSTER(기본 mini-mes-demo)
# 터널 인증 정보(~/.cloudflared/*.json, cert.pem)와 생성된 config.yml은 레포 밖에 둔다.
set -euo pipefail

HOST="${HOST:-mes.pmhllll12.cloud}"
TUNNEL="${TUNNEL:-mini-mes-demo}"
CLUSTER="${CLUSTER:-mini-mes-demo}"
CONTAINER="cloudflared-mini-mes"
CF_DIR="$HOME/.cloudflared"

[[ -f "$CF_DIR/cert.pem" ]] || { echo "$CF_DIR/cert.pem 없음 → cloudflared tunnel login 먼저"; exit 1; }
docker network inspect "k3d-$CLUSTER" >/dev/null || { echo "k3d 클러스터 $CLUSTER 없음 (k3d cluster start $CLUSTER)"; exit 1; }

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

# 컨테이너 안 경로 기준 설정. Traefik 인증서는 자체 서명이라 검증은 끄되, SNI는 실제 호스트로 보내 IngressRoute에 맞춘다
cat > "$CF_DIR/config.yml" <<EOF
tunnel: $ID
credentials-file: /etc/cloudflared/$ID.json
ingress:
  - hostname: $HOST
    service: https://k3d-$CLUSTER-serverlb:443
    originRequest:
      noTLSVerify: true
      originServerName: $HOST
  - service: http_status:404
EOF

# $HOST → <id>.cfargotunnel.com CNAME (이미 이 터널을 가리키면 그대로 둠)
cloudflared tunnel route dns "$TUNNEL" "$HOST"

# WSL 터미널이 닫혀도 계속 돌도록 서비스 대신 Docker 컨테이너로 (Docker가 다시 켜지면 자동 재시작)
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run -d --name "$CONTAINER" --restart unless-stopped \
  --network "k3d-$CLUSTER" --user "$(id -u):$(id -g)" \
  -v "$CF_DIR:/etc/cloudflared:ro" \
  cloudflare/cloudflared:latest tunnel --no-autoupdate --config /etc/cloudflared/config.yml run "$TUNNEL" >/dev/null

for _ in $(seq 30); do
  if docker logs "$CONTAINER" 2>&1 | grep -q "Registered tunnel connection"; then
    echo "연결됨 → https://$HOST/chat"
    exit 0
  fi
  sleep 2
done
docker logs --tail 20 "$CONTAINER"
echo "터널 연결을 확인하지 못함"
exit 1
