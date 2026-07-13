# DATA ENGINE — Terraform Variables

variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Deployment environment (production, staging)"
  type        = string
  default     = "production"
  validation {
    condition     = contains(["production", "staging"], var.environment)
    error_message = "Environment must be 'production' or 'staging'."
  }
}

variable "vpc_cidr" {
  description = "CIDR block for the VPC"
  type        = string
  default     = "10.0.0.0/16"
}

variable "private_subnet_a_cidr" {
  description = "CIDR for private subnet A (DB node)"
  type        = string
  default     = "10.0.1.0/24"
}

variable "private_subnet_b_cidr" {
  description = "CIDR for private subnet B"
  type        = string
  default     = "10.0.2.0/24"
}

variable "public_subnet_a_cidr" {
  description = "CIDR for public subnet A (compute node)"
  type        = string
  default     = "10.0.10.0/24"
}

variable "ami_id" {
  description = "AMI ID for EC2 instances (Ubuntu 22.04 LTS recommended)"
  type        = string
}

variable "compute_instance_type" {
  description = "EC2 instance type for Node 1 (compute-optimised)"
  type        = string
  default     = "c6i.2xlarge"
}

variable "db_instance_type" {
  description = "EC2 instance type for Node 2 (memory-optimised DB)"
  type        = string
  default     = "r6i.2xlarge"
}

variable "key_pair_name" {
  description = "EC2 key pair name for SSH access"
  type        = string
}
