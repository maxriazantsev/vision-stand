# Vision Stand

A Raspberry Pi with a camera. Real-time ROS 2 object detection on the
device, releases built from git tags, over-the-air updates and automatic
rollback when a release breaks.

## Architecture

```
git tag vX.Y.Z
   │
   │  publish a GitHub Release
   ▼
GitHub Release
   │
   │  CI builds and pushes the image
   ▼
container image (on GitHub's registry)
   │
   │  polled every 2 minutes
   ▼
agent/updater.py
   │
   │  pulls the new image, restarts the service
   ▼
vision-stand.service (the perception/ ROS graph)
   │
   │  health check
   ▼
   ├─ pass → stays on it
   └─ fail → rolls back to the previous version
```

- `perception/`: the ROS 2 workspace. Camera capture, motion detection,
  on-device object detection and the version overlay.
- `agent/`: `updater.py`, the device-side process that pulls new releases
  and rolls back on a failed health check.
- `deploy/`: provisioning script, systemd units and the per-device env
  template.

## The detection pipeline

| Node                   | What it does                                                                                                                                                                                                                     |
|------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `camera_node`          | Reads a video file or camera and publishes frames.                                                                                                                                                                               |
| `detector_node`        | Frame-differencing motion detector.                                                                                                                                                                                              |
| `object_detector_node` | Runs YOLOv8n on each frame, publishes detections plus an annotated video stream and overlays the running version. It also exposes a `/detector/take_snapshot` service that freezes the current frame and its detections to disk. |

## Quickstart

```bash
git clone <repo-url> vision-stand && cd vision-stand
source scripts/env.sh   # ROS environment + this project's ROS_DOMAIN_ID
cd perception
colcon build
source install/setup.bash
ros2 launch detector pipeline.launch.py
```

By default this runs against the bundled `test_clip.mp4`. To use a real
camera instead:

```bash
ros2 launch detector pipeline.launch.py source:=0
```

## Releases

A git tag is the version. There's nothing to hand-edit:

1. Cut a GitHub Release with tag `vX.Y.Z`.
2. CI builds the image and publishes `ghcr.io/maxriazantsev/vision-stand:vX.Y.Z`.
3. Each device polls the GitHub Releases API every two minutes and pulls
   whatever's new.

## Rollback

Two ways, depending on who notices the problem first:

- **Manual**: mark the bad release as a pre-release on GitHub. The
  Releases API's "latest" skips pre-releases, so every device still
  following latest falls back to the one before it on its next poll.
- **Automatic**: after every swap, the device runs a health check against
  the freshly started container. If it fails, the device reverts to
  whatever it was running before, on its own, without waiting on a human.

A device can also be pinned to one version instead of following latest, by
setting `PINNED_VERSION` in its `/etc/vision-stand/device.env`.

## Provisioning a new device

```bash
deploy/provision_device.sh <ssh-host> <vision-stand.service|vision-stand-sim.service>
```

Needs only Docker on the target; installs it if missing, detects the
camera's stable `/dev/v4l/by-id/...` path and lays down the systemd units
and the updater. `vision-stand-sim.service` runs against the bundled test
clip instead of a camera, for a device-free dry run of the same update path.

Every device also gets `vision-stand-foxglove.service`, a bridge that
exposes the live ROS topics on port 8765. Connect to it with
[Lichtblick](https://github.com/lichtblick-suite/lichtblick), to
see the annotated feed in real time.
