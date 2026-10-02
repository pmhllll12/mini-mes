#!/usr/bin/env bash
# 공개 데모 시연 영상 녹화: 가상 디스플레이에 브라우저를 띄워 record.mjs 시나리오를 실행하고 ffmpeg로 화면을 녹화
# 준비: npm install && npx playwright install chromium (Xvfb·ffmpeg 필요)
# 사용법: xvfb-run -a -s "-screen 0 1920x1080x24" tools/demo-video/rec.sh [출력 파일, 기본 out.mp4]
#   MOCK=1 이면 /query를 가짜 응답으로 대신 (LLM 호출·하루 질문 한도 소모 없이 화면 흐름 확인용)
#   BASE=https://... 로 대상 주소 변경 (기본 https://mes.pmhllll12.cloud)
set -euo pipefail
cd "$(dirname "$0")"
OUT="${1:-out.mp4}"
rm -f ready stop
node record.mjs & NODE=$!
until [[ -f ready ]] || ! kill -0 $NODE 2>/dev/null; do sleep 0.1; done
ffmpeg -y -v error -f x11grab -video_size 1920x1080 -framerate 30 -draw_mouse 0 -i "$DISPLAY" \
  -c:v libx264 -preset veryfast -crf 18 -pix_fmt yuv420p "$OUT" &
FF=$!
until [[ -f stop ]] || ! kill -0 $NODE 2>/dev/null; do sleep 0.1; done
kill -INT $FF; wait $FF || true
wait $NODE || status=$?
exit "${status:-0}"
