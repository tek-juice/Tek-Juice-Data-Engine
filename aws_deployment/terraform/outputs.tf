# ============================================================
# Tek Juice Data Engine — Terraform Outputs
# ============================================================

output "instance_id" {
  description = "EC2 instance ID"
  value       = aws_instance.app.id
}

output "public_ip" {
  description = "Elastic IP (static public IP of the server)"
  value       = aws_eip.app.public_ip
}

output "app_url" {
  description = "Direct app URL on port 9600"
  value       = "http://${aws_eip.app.public_ip}:9600"
}

output "ssh_command" {
  description = "SSH command to connect to your server"
  value       = "ssh -i ~/.ssh/id_rsa ubuntu@${aws_eip.app.public_ip}"
}

output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}
