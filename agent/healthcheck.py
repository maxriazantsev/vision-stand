#!/usr/bin/env python3
"""Health signal for the deploy pipeline. Run inside the just-started
container (`docker exec ... healthcheck.py`) right after a version swap;
passes once a few frames arrive on /camera/image_raw.

Catches a crashed node, a camera that failed to open, or ROS never coming
up. Does NOT catch the detector publishing wrong/garbage results — frame
arrival says nothing about whether the model output is correct.
"""
import sys

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image

TIMEOUT_S = 10.0
MIN_FRAMES = 3


class HealthCheck(Node):
    def __init__(self):
        super().__init__('health_check')
        self.count = 0
        self.create_subscription(Image, '/camera/image_raw', self._on_frame, 10)

    def _on_frame(self, _msg):
        self.count += 1


def main():
    rclpy.init()
    node = HealthCheck()

    deadline = node.get_clock().now().nanoseconds / 1e9 + TIMEOUT_S
    while node.count < MIN_FRAMES and node.get_clock().now().nanoseconds / 1e9 < deadline:
        rclpy.spin_once(node, timeout_sec=0.5)

    count = node.count
    node.destroy_node()
    rclpy.shutdown()

    if count >= MIN_FRAMES:
        print(f'OK: received {count} frames on /camera/image_raw')
        sys.exit(0)
    else:
        print(f'FAIL: received only {count} frames in {TIMEOUT_S}s')
        sys.exit(1)


if __name__ == '__main__':
    main()
