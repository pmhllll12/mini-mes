# 비용 안전장치 2: 할당량(quota) 정책으로 상시 무료 범위를 넘는 자원은 생성 자체를 거부한다
# 예산(budget.tf)은 과금 후 알림이고, 할당량은 콘솔·API·Terraform 어디서 만들든 생성 요청 단계에서 막는다
# 같은 자원을 대상으로 한 문장은 뒤의 것이 앞의 것을 덮어쓰므로 "전부 0 → 무료분만 허용" 순서로 쓴다
# 기존 자원에는 영향이 없고, 이미 쓰는 양(1 OCPU / 6GB / 부트 50GB)은 한도 안이다
# 데이터 전송량(월 10TB 무료)처럼 할당량 대상이 아닌 과금은 예산 알림으로만 잡힌다
resource "oci_limits_quota" "free_tier" {
  compartment_id = var.tenancy_ocid # 할당량 정책은 루트 compartment에 만든다
  name           = "mini-mes-free-tier"
  description    = "mini-mes: 상시 무료 범위를 넘는 자원 생성 차단"
  statements = [
    # VM: A1만 2 OCPU / 12GB까지 (GPU·x86 등 나머지 유료 shape는 0)
    "zero compute-core quotas in tenancy",
    "set compute-core quota standard-a1-core-count to 2 in tenancy",
    "zero compute-memory quotas in tenancy",
    "set compute-memory quota standard-a1-memory-count to 12 in tenancy",
    "zero compute quotas in tenancy", # 구형 shape 단위 할당량
    # 블록·부트 볼륨 합계 200GB, 백업 5개 (상시 무료 범위)
    "set block-storage quota total-storage-gb to 200 in tenancy",
    "set block-storage quota backup-count to 5 in tenancy",
    # 쓰지 않는 유료 서비스
    "zero load-balancer quotas in tenancy",
    "zero database quotas in tenancy",
  ]
}
