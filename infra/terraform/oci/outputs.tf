output "public_ip" {
  value = oci_core_instance.k3s.public_ip
}

output "ssh" {
  value = "ssh ubuntu@${oci_core_instance.k3s.public_ip}"
}

output "kube_tunnel" {
  description = "로컬 16443 → 서버 K3s API. 켜 둔 채로 infra/k3s/deploy.sh 실행"
  value       = "ssh -N -L 16443:127.0.0.1:6443 ubuntu@${oci_core_instance.k3s.public_ip}"
}
