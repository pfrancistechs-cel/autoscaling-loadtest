#!/usr/bin/env bash
# One-time EC2 setup: apt update/upgrade, base packages, Docker Engine + Compose v2, k6 (downloaded from GitHub),
# and the executable bit on every script. Safe to re-run.
set -euo pipefail
cd "$(dirname "$0")/.."

K6_VERSION=0.54.0

. /etc/os-release
if [ "${ID:-}" != "ubuntu" ]; then
  echo "This script supports Ubuntu 22.04 / 24.04 only (found: ${PRETTY_NAME:-unknown})." >&2
  exit 1
fi

echo "==> Making scripts executable"
chmod +x scripts/*.sh scripts/*.py

echo "==> Updating apt and upgrading packages"
sudo apt-get update -y
sudo DEBIAN_FRONTEND=noninteractive apt-get upgrade -y

echo "==> Installing base packages"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates curl git gnupg openssl python3 python3-yaml unzip

install_official_docker() {
  sudo install -m 0755 -d /etc/apt/keyrings || return 1
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg |
    sudo gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg || return 1
  sudo chmod a+r /etc/apt/keyrings/docker.gpg || return 1
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" |
    sudo tee /etc/apt/sources.list.d/docker.list >/dev/null || return 1
  sudo apt-get update -y || return 1
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin || return 1
}

install_ubuntu_docker() { # fallback if download.docker.com is unreachable
  sudo apt-get update -y || return 1
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2 || return 1
}

if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
  echo "==> Docker and Compose already installed"
else
  echo "==> Installing Docker Engine and Compose v2"
  if ! install_official_docker; then
    echo "!! Official Docker repo failed, falling back to Ubuntu's docker.io package"
    install_ubuntu_docker
  fi
fi

sudo systemctl enable --now docker
sudo usermod -aG docker "$USER" || true

case "$(uname -m)" in x86_64) ARCH=amd64 ;; aarch64) ARCH=arm64 ;; *) echo "Unsupported CPU: $(uname -m)" >&2; exit 1 ;; esac
if command -v k6 >/dev/null 2>&1 && [[ "$(k6 version 2>/dev/null)" == *"v$K6_VERSION"* ]]; then
  echo "==> k6 $K6_VERSION already installed"
else
  echo "==> Installing k6 $K6_VERSION"
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  curl -fsSL -o "$TMP/k6.tgz" "https://github.com/grafana/k6/releases/download/v${K6_VERSION}/k6-v${K6_VERSION}-linux-${ARCH}.tar.gz" ||
    { echo "Could not download k6. Check the outbound security-group rule and re-run." >&2; exit 1; }
  tar -xzf "$TMP/k6.tgz" -C "$TMP" --strip-components=1
  sudo install -m 0755 "$TMP/k6" /usr/local/bin/k6
fi

echo
echo "==> Versions"
sudo docker --version
sudo docker compose version
k6 version
git --version
python3 --version
echo
echo "Setup complete. (The 'docker' group applies after you log out and back in;"
echo "the scripts use sudo automatically until then.)"
