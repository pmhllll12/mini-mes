#!/usr/bin/env bash
# A1(무료 ARM) 재고 부족("Out of host capacity")이면 일정 간격으로 apply를 재시도한다.
# 재고 부족 외의 오류는 바로 멈춘다 (설정 오류를 반복 호출하지 않도록).
# 사용법: infra/terraform/oci/retry-apply.sh [간격(초), 기본 300]
#   사양을 줄여 시도: TF_VAR_ocpus=1 TF_VAR_memory_gb=6 infra/terraform/oci/retry-apply.sh
set -uo pipefail

INTERVAL="${1:-300}"
cd "$(dirname "$0")"

attempt=0
while true; do
  attempt=$((attempt + 1))
  echo "[$(date '+%F %T')] 시도 $attempt"
  out="$(terraform apply -input=false -auto-approve -no-color 2>&1)"
  if [[ $? -eq 0 ]]; then
    echo "$out" | tail -8
    echo "[$(date '+%F %T')] 생성 완료"
    exit 0
  fi
  if ! grep -q "Out of host capacity" <<<"$out"; then
    echo "$out" | tail -30
    echo "[$(date '+%F %T')] 재고 부족이 아닌 오류라 중단"
    exit 1
  fi
  echo "  재고 부족 - ${INTERVAL}초 후 재시도"
  sleep "$INTERVAL"
done
