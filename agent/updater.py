#!/usr/bin/env python3
"""Fleet update reconciler. Runs on the host (manages Docker itself, not
inside a container) as a systemd oneshot service woken periodically by
vision-stand-updater.timer.

Pull-only: this device asks GitHub "what's the latest release?" (or follows
a pin). Nothing ever reaches inbound into the device, so an offline device
just catches up whenever it next wakes. Rollback is the same shape either
way: a human marks a bad release as a pre-release on GitHub and /releases/
latest falls back to the one before it, or the device notices its own
health check failing right after a swap and reverts on its own.
"""
import json
import logging
import subprocess
import time
import urllib.request

DEVICE_ENV_FILE = '/etc/vision-stand/device.env'
CURRENT_VERSION_FILE = '/var/lib/vision-stand/current'
BAD_VERSIONS_FILE = '/var/lib/vision-stand/bad'
RELEASES_LATEST_URL = (
    'https://api.github.com/repos/maxriazantsev/vision-stand/releases/latest'
)
IMAGE_REPO = 'ghcr.io/maxriazantsev/vision-stand'
APP_CONTAINER = 'vision-stand'
VERSIONED_SERVICES = ('vision-stand.service', 'vision-stand-foxglove.service')

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger('updater')


def read_env_file(path):
    env = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, value = line.split('=', 1)
                env[key] = value
    except FileNotFoundError:
        pass
    return env


def desired_version():
    """A pin always wins. Otherwise, ask GitHub for the latest release.
    /releases/latest already excludes drafts and pre-releases, so marking a
    bad release as a pre-release is all a manual rollback takes.
    """
    pinned = read_env_file(DEVICE_ENV_FILE).get('PINNED_VERSION', '').strip()
    if pinned:
        return pinned
    with urllib.request.urlopen(RELEASES_LATEST_URL, timeout=10) as resp:
        return json.load(resp)['tag_name']


def current_tag():
    result = subprocess.run(
        ['docker', 'inspect', '--format', '{{.Config.Image}}', APP_CONTAINER],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None
    image = result.stdout.strip()
    return image.rsplit(':', 1)[-1] if ':' in image else None


CONTAINER_START_TIMEOUT_S = 15


def wait_for_container_running():
    deadline = time.monotonic() + CONTAINER_START_TIMEOUT_S
    while time.monotonic() < deadline:
        result = subprocess.run(
            ['docker', 'inspect', '--format', '{{.State.Running}}', APP_CONTAINER],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip() == 'true':
            return True
        time.sleep(1)
    return False


def run_health_check():
    if not wait_for_container_running():
        log.error(f'{APP_CONTAINER} did not start within '
                   f'{CONTAINER_START_TIMEOUT_S}s')
        return False
    result = subprocess.run(
        ['docker', 'exec', APP_CONTAINER,
         '/entrypoint.sh', 'python3', '/workspace/healthcheck.py'],
        timeout=30,
    )
    return result.returncode == 0


def write_current_version(tag):
    with open(CURRENT_VERSION_FILE, 'w') as f:
        f.write(f'{tag}\n')


def read_bad_versions():
    try:
        with open(BAD_VERSIONS_FILE) as f:
            return {line.strip() for line in f if line.strip()}
    except FileNotFoundError:
        return set()


def record_bad_version(tag):
    with open(BAD_VERSIONS_FILE, 'a') as f:
        f.write(f'{tag}\n')


def swap_to(tag):
    """Pulls `tag`, writes it to CURRENT_VERSION_FILE (the systemd units
    read that file to pick which image to run, so it has to be written
    before the restart), restarts the versioned services and health-checks
    the result. Returns whether the health check passed. The file is not
    reverted on failure; the caller rolls back by calling swap_to() again
    with the previous tag.
    """
    image = f'{IMAGE_REPO}:{tag}'
    log.info(f'Pulling {image}')
    if subprocess.run(['docker', 'pull', image]).returncode != 0:
        log.error(f'Failed to pull {image}, does that tag exist?')
        return False

    write_current_version(tag)

    for service in VERSIONED_SERVICES:
        log.info(f'Restarting {service} on {tag}')
        subprocess.run(['systemctl', 'restart', service], check=True)

    healthy = run_health_check()
    log.info(f"Health check after swap to {tag}: {'PASS' if healthy else 'FAIL'}")
    return healthy


def main():
    previous = current_tag()
    try:
        desired = desired_version()
    except OSError:
        # URLError, HTTPError and socket timeouts are all OSErrors.
        log.error(f'Could not reach GitHub, staying on {previous!r}')
        return

    if desired in read_bad_versions():
        log.info(f'Skipping {desired}, it failed its health check before')
        return

    if previous == desired:
        log.info(f'Already on {desired}, nothing to do')
        return

    log.info(f'Desired={desired} running={previous!r}, updating')
    if swap_to(desired):
        return

    if current_tag() == previous:
        # Never actually left `previous` (the pull itself failed), so
        # there's nothing to roll back, just leave it running.
        log.error(f'{desired} never came up, leaving {previous!r} running')
        return

    if not previous:
        log.error(f'{desired} failed its health check and there is no '
                   f'previous version to roll back to')
        return

    log.error(f'{desired} failed its health check, rolling back to {previous}')
    record_bad_version(desired)
    if not swap_to(previous):
        log.error(f'Rollback to {previous} also failed its health check')


if __name__ == '__main__':
    main()
