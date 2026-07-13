# DATA ENGINE — Terraform Infrastructure
# Phase 4: Production Cloud Deployment
# Two-node architecture: compute-optimised + memory-optimised DB

terraform {
  required_version = ">= 1.8.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
  backend "s3" {
    bucket = "data-engine-terraform-state"
    key    = "production/terraform.tfstate"
    region = "us-east-1"
  }
}

provider "aws" {
  region = var.aws_region
}

# ── VPC ───────────────────────────────────────────────────────────────────────
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = {
    Name        = "data-engine-vpc"
    Environment = var.environment
  }
}

resource "aws_subnet" "private_a" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnet_a_cidr
  availability_zone = "${var.aws_region}a"
  tags = { Name = "data-engine-private-a" }
}

resource "aws_subnet" "private_b" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnet_b_cidr
  availability_zone = "${var.aws_region}b"
  tags = { Name = "data-engine-private-b" }
}

resource "aws_subnet" "public_a" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_a_cidr
  availability_zone       = "${var.aws_region}a"
  map_public_ip_on_launch = true
  tags = { Name = "data-engine-public-a" }
}

# ── Internet Gateway ──────────────────────────────────────────────────────────
resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "data-engine-igw" }
}

# ── Security Groups ───────────────────────────────────────────────────────────
resource "aws_security_group" "compute_node" {
  name        = "data-engine-compute-node"
  description = "Node 1: Compute-optimised — Python APIs and workers"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port   = 8000
    to_port     = 8013
    protocol    = "tcp"
    cidr_blocks = [var.vpc_cidr]
    description = "Internal service ports"
  }
  ingress {
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTP"
  }
  ingress {
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
    description = "HTTPS"
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "data-engine-compute-sg" }
}

resource "aws_security_group" "db_node" {
  name        = "data-engine-db-node"
  description = "Node 2: Memory-optimised — PostgreSQL + PGVector"
  vpc_id      = aws_vpc.main.id

  ingress {
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.compute_node.id]
    description     = "PostgreSQL from compute node only"
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
  tags = { Name = "data-engine-db-sg" }
}

# ── Node 1: Compute-Optimised ─────────────────────────────────────────────────
resource "aws_instance" "compute_node" {
  ami                    = var.ami_id
  instance_type          = var.compute_instance_type  # e.g. c6i.2xlarge
  subnet_id              = aws_subnet.public_a.id
  vpc_security_group_ids = [aws_security_group.compute_node.id]
  key_name               = var.key_pair_name
  iam_instance_profile   = aws_iam_instance_profile.compute_node.name

  root_block_device {
    volume_type = "gp3"
    volume_size = 100
    encrypted   = true
  }

  user_data = templatefile("${path.module}/scripts/compute_node_init.sh", {
    environment = var.environment
  })

  tags = {
    Name        = "data-engine-compute-node"
    Role        = "compute"
    Environment = var.environment
  }
}

# ── Node 2: Memory-Optimised DB ───────────────────────────────────────────────
resource "aws_instance" "db_node" {
  ami                    = var.ami_id
  instance_type          = var.db_instance_type  # e.g. r6i.2xlarge
  subnet_id              = aws_subnet.private_a.id
  vpc_security_group_ids = [aws_security_group.db_node.id]
  key_name               = var.key_pair_name

  root_block_device {
    volume_type = "gp3"
    volume_size = 500
    iops        = 3000
    throughput  = 125
    encrypted   = true
  }

  user_data = templatefile("${path.module}/scripts/db_node_init.sh", {
    environment = var.environment
  })

  tags = {
    Name        = "data-engine-db-node"
    Role        = "database"
    Environment = var.environment
  }
}

# ── Secrets Manager ───────────────────────────────────────────────────────────
resource "aws_secretsmanager_secret" "app_secrets" {
  name                    = "data-engine/${var.environment}/app-secrets"
  recovery_window_in_days = 7
  tags                    = { Environment = var.environment }
}

# ── IAM ───────────────────────────────────────────────────────────────────────
resource "aws_iam_role" "compute_node" {
  name = "data-engine-compute-role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_instance_profile" "compute_node" {
  name = "data-engine-compute-profile"
  role = aws_iam_role.compute_node.name
}
