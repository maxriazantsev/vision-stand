#!/usr/bin/env bash
# One-time setup for a new fleet device, run from the operator's machine
# over SSH. The device itself never needs this repo, ROS, or a build
# toolchain — just Docker, plus the small set of files copied over below.
#
# Usage: deploy/provision_device.sh <ssh-host> <app-unit>
#   <ssh-host>   SSH alias/host for the device (e.g. pi)
#   <app-unit>   vision-stand.service (real camera) or vision-stand-sim.service (file source)
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "Usage: $0 <ssh-host> <vision-stand.service|vision-stand-sim.service>" >&2
  exit 1
fi

host="$1"
app_unit="$2"
repo_root="$(cd "$(dirname "$0")/.." && pwd)"

ssh "$host" "sudo mkdir -p /etc/vision-stand /opt/vision-stand /var/lib/vision-stand"

scp "$repo_root/agent/updater.py" "$host:/tmp/updater.py"
scp "$repo_root/deploy/device.env.example" "$host:/tmp/device.env.example"
scp "$repo_root/deploy/systemd/$app_unit" "$host:/tmp/vision-stand.service"
scp "$repo_root/deploy/systemd/vision-stand-foxglove.service" "$host:/tmp/"
scp "$repo_root/deploy/systemd/vision-stand-updater.service" "$host:/tmp/"
scp "$repo_root/deploy/systemd/vision-stand-updater.timer" "$host:/tmp/"
if [[ "$app_unit" == "vision-stand-sim.service" ]]; then
  scp "$repo_root/perception/test_clip.mp4" "$host:/tmp/test_clip.mp4"
fi

ssh "$host" bash -s "$app_unit" <<'REMOTE'
set -euo pipefail
app_unit="$1"

if ! command -v docker >/dev/null; then
  echo "Installing Docker..."
  sudo apt-get update
  sudo apt-get install -y docker.io
  sudo systemctl enable --now docker
fi

[[ -f /etc/vision-stand/device.env ]] || sudo cp /tmp/device.env.example /etc/vision-stand/device.env
[[ -f /var/lib/vision-stand/current ]] || sudo touch /var/lib/vision-stand/current
[[ -f /var/lib/vision-stand/bad ]] || sudo touch /var/lib/vision-stand/bad

if [[ "$app_unit" == "vision-stand.service" ]]; then
  camera_source=$(ls /dev/v4l/by-id/*-video-index0 2>/dev/null | head -1 || true)
  if [[ -z "$camera_source" ]]; then
    echo "WARNING: no /dev/v4l/by-id/*-video-index0 found, falling back to /dev/video0 (not hotplug-safe)" >&2
    camera_source="/dev/video0"
  fi
  sudo sed -i '/^CAMERA_SOURCE=/d' /etc/vision-stand/device.env
  echo "CAMERA_SOURCE=$camera_source" | sudo tee -a /etc/vision-stand/device.env >/dev/null
  echo "Detected camera: $camera_source"
fi

sudo mv /tmp/updater.py /opt/vision-stand/updater.py
sudo chmod +x /opt/vision-stand/updater.py

if [[ "$app_unit" == "vision-stand-sim.service" ]]; then
  sudo mv /tmp/test_clip.mp4 /opt/vision-stand/test_clip.mp4
fi

sudo mv /tmp/vision-stand.service /etc/systemd/system/vision-stand.service
sudo mv /tmp/vision-stand-foxglove.service /etc/systemd/system/
sudo mv /tmp/vision-stand-updater.service /etc/systemd/system/
sudo mv /tmp/vision-stand-updater.timer /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now vision-stand.service
sudo systemctl enable --now vision-stand-foxglove.service
sudo systemctl enable --now vision-stand-updater.timer

echo "Provisioned."
REMOTE

echo "Next: ssh $host sudo systemctl start vision-stand-updater.service"
echo "That pulls the latest published GitHub release. To pin this device to"
echo "one version instead, set PINNED_VERSION in /etc/vision-stand/device.env."
