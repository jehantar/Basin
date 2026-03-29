#!/usr/bin/env bash
# Basin VM bootstrap — run once on Ubuntu 24.04 as root
set -euo pipefail

echo "=== Adding 1GB swap file ==="
if [ ! -f /swapfile ]; then
    fallocate -l 1G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
    echo "Swap created."
else
    echo "Swap already exists, skipping."
fi

echo "=== Installing Docker Engine ==="
if ! command -v docker &> /dev/null; then
    apt-get update
    apt-get install -y --no-install-recommends ca-certificates curl gnupg
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    echo \
      "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu \
      $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
      tee /etc/apt/sources.list.d/docker.list > /dev/null
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    echo "Docker installed."
else
    echo "Docker already installed, skipping."
fi

echo "=== Creating basin user ==="
if ! id -u basin &>/dev/null; then
    useradd -r -s /bin/bash -d /opt/basin -m basin
    usermod -aG docker basin
    echo "basin user created."
else
    echo "basin user already exists, skipping."
fi

echo "=== Creating directories ==="
mkdir -p /opt/basin/{data/hevy/drop,data/healthkit/imports,data/healthkit/failed,certs/teller,backups}
chown -R basin:basin /opt/basin

echo "=== Configuring Docker log rotation ==="
cat > /etc/docker/daemon.json << 'DAEMON'
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "10m",
    "max-file": "3"
  }
}
DAEMON
systemctl restart docker

echo ""
echo "=== Bootstrap complete ==="
echo "Next steps:"
echo "1. Clone Basin repo to /opt/basin/"
echo "2. Add .env with op:// references"
echo "3. Place Teller certs in /opt/basin/certs/teller/"
echo "4. Run: cd /opt/basin && op run --env-file=.env -- docker compose up -d"
