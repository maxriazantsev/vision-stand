import time

import cv2
import rclpy
from rclpy.node import Node
from rcl_interfaces.msg import SetParametersResult
from sensor_msgs.msg import Image
from cv_bridge import CvBridge

RECONNECT_FAILURE_THRESHOLD = 10
RECONNECT_RETRY_INTERVAL_S = 1.0


class CameraNode(Node):
    """Reads frames from a source (video file, camera index, or /dev/...
    path) and publishes them to /camera/image_raw.

    Prefer a stable /dev/v4l/by-id/... path over a numeric index for a real
    camera: USB re-enumeration can move which /dev/videoN a camera lands on,
    but udev keeps the by-id symlink pointing at the right device.
    """

    def __init__(self):
        super().__init__('camera_node')

        # Parameters
        self.declare_parameter('source', '')
        self.declare_parameter('fps', 30.0)
        self.declare_parameter('loop', True)
        self.declare_parameter('frame_id', 'camera')
        self.declare_parameter('enabled', True)

        source = self.get_parameter('source').get_parameter_value().string_value
        self.fps = self.get_parameter('fps').get_parameter_value().double_value
        self.loop = self.get_parameter('loop').get_parameter_value().bool_value
        self.frame_id = \
            self.get_parameter('frame_id').get_parameter_value().string_value
        self.enabled = self.get_parameter('enabled').get_parameter_value().bool_value

        if not source:
            self.get_logger().error(
                "Parameter 'source' is empty. Pass a video file path, a "
                "camera index, or a /dev/... camera path, e.g. "
                "-p source:=/path/to/clip.mp4, -p source:=0, or "
                "-p source:=/dev/v4l/by-id/usb-...-video-index0")
            raise SystemExit(1)

        self.source = int(source) if source.isdigit() else source
        self.is_camera = isinstance(self.source, int) or \
            (isinstance(self.source, str) and self.source.startswith('/dev/'))

        self.bridge = CvBridge()
        self.publisher = self.create_publisher(Image, '/camera/image_raw', 10)

        self.consecutive_failures = 0
        self.last_reconnect_attempt = 0.0

        self.cap = None
        if self.enabled:
            self.cap = self._open_capture()
            if not self.cap.isOpened():
                self.get_logger().error(f'Failed to open source: {self.source!r}')
                raise SystemExit(1)

        self.get_logger().info(
            f'Publishing frames from {self.source!r} '
            f'(camera={self.is_camera}, loop={self.loop}, enabled={self.enabled}) '
            f'at {self.fps} fps on /camera/image_raw')

        self.add_on_set_parameters_callback(self._on_set_parameters)
        self.timer = self.create_timer(1.0 / self.fps, self.tick)

    def _on_set_parameters(self, params):
        """Lets `enabled` be toggled live to release/reacquire the capture
        device without restarting the node.
        """
        for param in params:
            if param.name == 'enabled' and param.value != self.enabled:
                self.enabled = param.value
                if self.enabled:
                    self.get_logger().info('Enabled -- opening capture device')
                    self.cap = self._open_capture()
                    self.consecutive_failures = 0
                else:
                    self.get_logger().info('Disabled -- releasing capture device')
                    if self.cap is not None:
                        self.cap.release()
                    self.cap = None
        return SetParametersResult(successful=True)

    def _open_capture(self):
        cap = cv2.VideoCapture(self.source, cv2.CAP_V4L2) if self.is_camera \
            else cv2.VideoCapture(self.source)
        if self.is_camera:
            cap.set(cv2.CAP_PROP_FPS, self.fps)
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 4)
        return cap

    def tick(self):
        if not self.enabled:
            return

        ok, frame = self.cap.read()
        if not ok:
            # End of a video file: rewind and keep streaming.
            if not self.is_camera and self.loop:
                self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = self.cap.read()
            if not ok:
                self.get_logger().warning('No frame read from source')
                self.consecutive_failures += 1
                if self.is_camera:
                    self._maybe_reconnect()
                return

        self.consecutive_failures = 0
        msg = self.bridge.cv2_to_imgmsg(frame, encoding='bgr8')
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self.frame_id
        self.publisher.publish(msg)

    def _maybe_reconnect(self):
        """Releases and reopens the capture after a USB unplug/replug."""
        if self.consecutive_failures < RECONNECT_FAILURE_THRESHOLD:
            return
        now = time.monotonic()
        if now - self.last_reconnect_attempt < RECONNECT_RETRY_INTERVAL_S:
            return
        self.last_reconnect_attempt = now

        self.get_logger().warning(
            f'{self.consecutive_failures} consecutive read failures, '
            f'attempting to reopen {self.source!r}')
        if self.cap is not None:
            self.cap.release()
        self.cap = self._open_capture()
        if self.cap.isOpened():
            self.get_logger().info('Reconnected to camera')
            self.consecutive_failures = 0
        else:
            self.get_logger().warning('Reopen failed, will keep retrying')

    def destroy_node(self):
        if getattr(self, 'cap', None) is not None:
            self.cap.release()
        super().destroy_node()


def main():
    rclpy.init()
    node = CameraNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
