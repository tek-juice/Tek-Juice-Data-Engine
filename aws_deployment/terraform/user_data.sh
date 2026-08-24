#!/bin/bash
# ============================================================
# EC2 User Data — Bootstrap script
# Runs once on first boot to set up the server
# ============================================================

set -e

# Update system
apt-get update -y
apt-get upgrade -y

# Install Docker
apt-get install -y ca-certificates curl gnupg lsb-release
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null
apt-get update -y
apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Add ubuntu user to docker group
usermod -aG docker ubuntu

# Install Git
apt-get install -y git

# Enable and start Docker
systemctl enable docker
systemctl start docker

# Create app directory
mkdir -p /opt/${project_name}
chown ubuntu:ubuntu /opt/${project_name}

# Open port 9600 in ufw
ufw allow 9600/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 22/tcp

echo "Bootstrap complete. Server ready for deployment on port ${app_port}."
