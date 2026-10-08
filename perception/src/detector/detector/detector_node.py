import cv2
import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_msgs.msg import Bool
from cv_bridge import CvBridge


class DetectorNode(Node):
    """Subscribes to /camera/image_raw and detects motion by differencing
    consecutive frames. Publishes std_msgs/Bool on /detector/motion and logs
    when motion appears.
    """

    def __init__(self):
        super().__init__('detector_node')

        # Parameters: thresholds are tunable without code changes.
        self.declare_parameter('diff_threshold', 25)       # per-pixel gray diff
        self.declare_parameter('motion_min_pixels', 500)   # changed-pixel count

        self.diff_threshold = \
            self.get_parameter('diff_threshold').get_parameter_value().integer_value
        self.motion_min_pixels = \
            self.get_parameter('motion_min_pixels').get_parameter_value().integer_value

        self.bridge = CvBridge()
        self.prev_gray = None
        self.motion_active = False  # for edge-triggered logging

        self.subscription = self.create_subscription(
            Image, '/camera/image_raw', self.on_image, 10)
        self.publisher = self.create_publisher(Bool, '/detector/motion', 10)

        self.get_logger().info(
            f'Detecting motion on /camera/image_raw '
            f'(diff_threshold={self.diff_threshold}, '
            f'motion_min_pixels={self.motion_min_pixels})')

    def on_image(self, msg):
        frame = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        motion = False
        if self.prev_gray is not None:
            delta = cv2.absdiff(self.prev_gray, gray)
            _, thresh = cv2.threshold(
                delta, self.diff_threshold, 255, cv2.THRESH_BINARY)
            changed = cv2.countNonZero(thresh)
            motion = changed >= self.motion_min_pixels

            # Edge-triggered logging: only on transition into motion.
            if motion and not self.motion_active:
                self.get_logger().info(f'Motion detected ({changed} px changed)')
            self.motion_active = motion

        self.prev_gray = gray

        out = Bool()
        out.data = bool(motion)
        self.publisher.publish(out)


def main():
    rclpy.init()
    node = DetectorNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
