# DATA ENGINE — Terraform Outputs

output "compute_node_public_ip" {
  description = "Public IP of the compute node (Node 1)"
  value       = aws_instance.compute_node.public_ip
}

output "db_node_private_ip" {
  description = "Private IP of the database node (Node 2)"
  value       = aws_instance.db_node.private_ip
}

output "vpc_id" {
  description = "VPC ID"
  value       = aws_vpc.main.id
}

output "secrets_manager_arn" {
  description = "ARN of the Secrets Manager secret"
  value       = aws_secretsmanager_secret.app_secrets.arn
}
