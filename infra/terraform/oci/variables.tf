variable "tenancy_ocid" {
  description = "테넌시 OCID (~/.oci/config의 tenancy 값)"
  type        = string
}

variable "compartment_ocid" {
  description = "리소스를 만들 compartment OCID. 비우면 테넌시(루트 compartment)"
  type        = string
  default     = ""
}

variable "region" {
  description = "OCI 리전 (Always Free A1은 홈 리전에서만 생성 가능)"
  type        = string
  default     = "ap-osaka-1"
}

variable "oci_profile" {
  description = "~/.oci/config 프로필 이름"
  type        = string
  default     = "DEFAULT"
}

variable "ssh_public_key_path" {
  description = "VM에 등록할 SSH 공개키 경로"
  type        = string
  default     = "~/.ssh/id_ed25519.pub"
}

variable "allowed_ssh_cidr" {
  description = "SSH(22)를 허용할 CIDR - 내 공인 IP/32. 0.0.0.0/0은 쓰지 않는다"
  type        = string

  validation {
    condition     = can(cidrhost(var.allowed_ssh_cidr, 0)) && var.allowed_ssh_cidr != "0.0.0.0/0"
    error_message = "allowed_ssh_cidr는 올바른 CIDR이어야 하고 0.0.0.0/0은 허용하지 않습니다 (예: 203.0.113.10/32)."
  }
}

# Always Free: A1.Flex 합계 2 OCPU / 12GB (2026-06-15에 4 OCPU / 24GB에서 줄어듦), 블록 볼륨 합계 200GB
variable "ocpus" {
  type    = number
  default = 2
}

variable "memory_gb" {
  type    = number
  default = 12
}

variable "boot_volume_gb" {
  type    = number
  default = 50
}

variable "availability_domain_index" {
  description = "A1 재고가 없다는 오류(Out of host capacity)가 나면 다른 AD 번호로 재시도 (리전에 AD가 1개면 0만 가능)"
  type        = number
  default     = 0
}

variable "k3s_version" {
  type    = string
  default = "v1.36.4+k3s1"
}

# 비용 안전장치 (budget.tf)
variable "budget_amount" {
  description = "월 예산 (계정 청구 통화 기준). 알림은 이 금액의 1% 실제 지출부터"
  type        = number
  default     = 1
}

variable "budget_alert_email" {
  description = "예산 알림 받을 이메일. 비우면 예산만 만들고 알림 규칙은 만들지 않음"
  type        = string
  default     = ""

  validation {
    condition     = var.budget_alert_email == "" || can(regex("^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$", var.budget_alert_email))
    error_message = "budget_alert_email은 이메일 형식이어야 합니다."
  }
}
