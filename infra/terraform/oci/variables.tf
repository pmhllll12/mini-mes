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

# Always Free: A1.Flex 합계 4 OCPU / 24GB, 블록 볼륨 합계 200GB
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
