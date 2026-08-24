# ============================================================
# Tek Juice Data Engine — Terraform Variables
# ============================================================

variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name used for resource naming"
  type        = string
  default     = "tek-juice-data-engine"
}

variable "environment" {
  description = "Deployment environment (production, staging, dev)"
  type        = string
  default     = "production"

  validation {
    condition     = contains(["production", "staging", "dev"], var.environment)
    error_message = "environment must be one of: production, staging, dev"
  }
}

variable "instance_type" {
  description = "EC2 instance type"
  type        = string
  default     = "t3.medium"
  # Recommended sizes:
  # t3.small   — dev/staging (2 vCPU, 2GB)
  # t3.medium  — small production (2 vCPU, 4GB)
  # t3.large   — medium production (2 vCPU, 8GB)
  # c5.xlarge  — compute-heavy (4 vCPU, 8GB)
}

variable "root_volume_size" {
  description = "Root EBS volume size in GB"
  type        = number
  default     = 30
}

variable "public_key_path" {
  description = "Path to your SSH public key file"
  type        = string
  default     = "~/.ssh/id_rsa.pub"
}

variable "allowed_ssh_cidr" {
  description = "CIDR block allowed to SSH into the instance (use your IP)"
  type        = string
  default     = "0.0.0.0/0"
  # IMPORTANT: restrict this to your IP in production
  # e.g. "203.0.113.5/32"
}
