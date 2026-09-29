terraform {
  required_version = ">= 1.9"

  required_providers {
    oci = {
      source  = "oracle/oci"
      version = "~> 9.7"
    }
  }
}

# 인증은 ~/.oci/config (API 키)에서 읽는다 - 키 값은 코드·tfvars에 두지 않는다
provider "oci" {
  region              = var.region
  config_file_profile = var.oci_profile
}
