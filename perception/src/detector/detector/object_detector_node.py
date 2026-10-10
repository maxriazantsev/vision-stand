import hashlib
import json
import os
import resource
import subprocess
import time
from datetime import datetime, timezone

import cv2
import numpy as np
import onnxruntime as ort
import rclpy
from ament_index_python.packages import get_package_share_directory
from cv_bridge import CvBridge
from rcl_interfaces.msg import SetParametersResult
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_srvs.srv import Trigger
from vision_msgs.msg import (
    BoundingBox2D,
    Detection2D,
    Detection2DArray,
    ObjectHypothesis,
    ObjectHypothesisWithPose,
)

from detector.coco_classes import COCO_CLASSES

LOG_INTERVAL_FRAMES = 30  # how often (in frames) to log fps/latency/memory
LETTERBOX_COLOR = (114, 114, 114)  # YOLO's own letterbox padding color
SUPPORTED_RESOLUTIONS = (320, 640)  # only sizes with a committed ONNX export


class ObjectDetectorNode(Node):
    """Runs a YOLOv8n ONNX model (CPU, via ONNX Runtime) on
    /camera/image_raw, publishing detections on /detector/objects and an
    annotated copy on /detector/objects/annotated.
    """

    def __init__(self):
        super().__init__('object_detector_node')

        self.declare_parameter('resolution', 640)
        self.declare_parameter('conf_threshold', 0.5)
        self.declare_parameter('iou_threshold', 0.45)
        self.declare_parameter('classes', ['person'])
        self.declare_parameter('snapshot_dir', '~/vision_stand_snapshots')

        self.resolution = \
            self.get_parameter('resolution').get_parameter_value().integer_value
        self.conf_threshold = \
            self.get_parameter('conf_threshold').get_parameter_value().double_value
        self.iou_threshold = \
            self.get_parameter('iou_threshold').get_parameter_value().double_value
        target_classes = \
            self.get_parameter('classes').get_parameter_value().string_array_value
        self.target_class_ids = {
            COCO_CLASSES.index(name) for name in target_classes if name in COCO_CLASSES
        }
        unknown = [name for name in target_classes if name not in COCO_CLASSES]
        if unknown:
            self.get_logger().warning(f'Ignoring unknown class name(s): {unknown}')

        self.snapshot_dir = os.path.expanduser(
            self.get_parameter('snapshot_dir').get_parameter_value().string_value)

        self.session, self.input_name, self.model_sha256 = \
            self._load_session(self.resolution)
        self.code_version = self._get_code_version()

        self.bridge = CvBridge()
        self.subscription = self.create_subscription(
            Image, '/camera/image_raw', self.on_image, 10)
        self.detections_pub = self.create_publisher(
            Detection2DArray, '/detector/objects', 10)
        self.annotated_pub = self.create_publisher(
            Image, '/detector/objects/annotated', 10)
        self.snapshot_srv = self.create_service(
            Trigger, '/detector/take_snapshot', self.on_take_snapshot)

        self.last_frame = None
        self.last_detections = None
        self.last_header = None

        self.frame_count = 0
        self.inference_time_total = 0.0
        self.window_start = time.monotonic()

        self.add_on_set_parameters_callback(self._on_set_parameters)

        classes_desc = sorted(target_classes) if self.target_class_ids else ['all']
        self.get_logger().info(
            f'Running YOLOv8n ONNX ({self.resolution}x{self.resolution}) on CPU, '
            f'classes={classes_desc}, conf_threshold={self.conf_threshold}, '
            f'iou_threshold={self.iou_threshold}')

    @staticmethod
    def _load_session(resolution):
        model_path = (
            f'{get_package_share_directory("detector")}/models/'
            f'yolov8n_{resolution}.onnx'
        )
        session = ort.InferenceSession(model_path, providers=['CPUExecutionProvider'])
        with open(model_path, 'rb') as f:
            sha256 = hashlib.sha256(f.read()).hexdigest()
        return session, session.get_inputs()[0].name, sha256

    @staticmethod
    def _get_code_version():
        env_version = os.environ.get('CODE_VERSION')
        if env_version:
            return env_version
        try:
            repo_dir = os.path.dirname(os.path.realpath(__file__))
            result = subprocess.run(
                ['git', '-C', repo_dir, 'rev-parse', 'HEAD'],
                capture_output=True, text=True, timeout=5, check=True)
            return result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return 'unknown'

    def _on_set_parameters(self, params):
        """Lets params be changed live (e.g. from Foxglove) without
        restarting the node.
        """
        for param in params:
            if param.name == 'resolution' and param.value not in SUPPORTED_RESOLUTIONS:
                return SetParametersResult(
                    successful=False,
                    reason=f'resolution must be one of {SUPPORTED_RESOLUTIONS} '
                           f'(only sizes with a committed ONNX export)')

        for param in params:
            if param.name == 'resolution' and param.value != self.resolution:
                self.session, self.input_name, self.model_sha256 = \
                    self._load_session(param.value)
                self.resolution = param.value
                self.get_logger().info(
                    f'Reloaded model at {self.resolution}x{self.resolution}')
            elif param.name == 'conf_threshold':
                self.conf_threshold = param.value
            elif param.name == 'iou_threshold':
                self.iou_threshold = param.value
            elif param.name == 'classes':
                self.target_class_ids = {
                    COCO_CLASSES.index(name) for name in param.value
                    if name in COCO_CLASSES
                }

        return SetParametersResult(successful=True)

    def on_image(self, msg):
        frame = self.bridge.imgmsg_to_cv2(msg, desired_encoding='bgr8')

        blob, scale, pad = self._letterbox(frame, self.resolution)

        t0 = time.monotonic()
        output = self.session.run(None, {self.input_name: blob})[0]
        inference_s = time.monotonic() - t0

        detections = self._postprocess(output, frame.shape, scale, pad)

        self.last_frame = frame
        self.last_detections = detections
        self.last_header = msg.header

        self._publish_detections(detections, msg.header)
        self._publish_annotated(frame, detections, msg.header)
        self._log_stats(inference_s)

    @staticmethod
    def _letterbox(frame, size):
        """Resize+pad to a size x size square (YOLO's expected input),
        returning the blob plus scale/padding to map boxes back later.
        """
        h, w = frame.shape[:2]
        scale = min(size / h, size / w)
        new_w, new_h = round(w * scale), round(h * scale)
        resized = cv2.resize(frame, (new_w, new_h), interpolation=cv2.INTER_LINEAR)

        pad_w, pad_h = size - new_w, size - new_h
        top, bottom = pad_h // 2, pad_h - pad_h // 2
        left, right = pad_w // 2, pad_w - pad_w // 2
        padded = cv2.copyMakeBorder(
            resized, top, bottom, left, right,
            cv2.BORDER_CONSTANT, value=LETTERBOX_COLOR)

        rgb = cv2.cvtColor(padded, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        blob = np.transpose(rgb, (2, 0, 1))[np.newaxis, ...]
        return blob, scale, (left, top)

    def _postprocess(self, output, frame_shape, scale, pad):
        """(1, 84, N) model output -> filtered, NMS'd (x1, y1, x2, y2,
        class_id, score) tuples in original-frame pixel coordinates.
        """
        h, w = frame_shape[:2]
        pad_x, pad_y = pad

        rows = output[0].T  # (N, 84)
        class_scores = rows[:, 4:]
        class_ids = np.argmax(class_scores, axis=1)
        scores = class_scores[np.arange(len(rows)), class_ids]

        keep = scores >= self.conf_threshold
        if self.target_class_ids:
            keep &= np.isin(class_ids, list(self.target_class_ids))
        rows, class_ids, scores = rows[keep], class_ids[keep], scores[keep]
        if len(rows) == 0:
            return []

        cx, cy, bw, bh = rows[:, 0], rows[:, 1], rows[:, 2], rows[:, 3]
        x1, y1 = cx - bw / 2, cy - bh / 2
        nms_boxes = np.stack([x1, y1, bw, bh], axis=1).tolist()

        indices = cv2.dnn.NMSBoxes(
            nms_boxes, scores.tolist(), self.conf_threshold, self.iou_threshold)
        if len(indices) == 0:
            return []
        indices = np.array(indices).flatten()

        detections = []
        for i in indices:
            # Undo letterbox padding/scale to map back to the original frame
            ox1 = np.clip((x1[i] - pad_x) / scale, 0, w)
            oy1 = np.clip((y1[i] - pad_y) / scale, 0, h)
            ox2 = np.clip((x1[i] + bw[i] - pad_x) / scale, 0, w)
            oy2 = np.clip((y1[i] + bh[i] - pad_y) / scale, 0, h)
            detections.append((ox1, oy1, ox2, oy2, int(class_ids[i]), float(scores[i])))
        return detections

    def _publish_detections(self, detections, header):
        msg = Detection2DArray()
        msg.header = header
        for x1, y1, x2, y2, class_id, score in detections:
            det = Detection2D()
            det.header = header
            det.bbox = BoundingBox2D()
            det.bbox.center.position.x = (x1 + x2) / 2
            det.bbox.center.position.y = (y1 + y2) / 2
            det.bbox.size_x = x2 - x1
            det.bbox.size_y = y2 - y1
            hyp = ObjectHypothesisWithPose()
            hyp.hypothesis = ObjectHypothesis()
            hyp.hypothesis.class_id = COCO_CLASSES[class_id]
            hyp.hypothesis.score = score
            det.results = [hyp]
            msg.detections.append(det)
        self.detections_pub.publish(msg)

    def _draw_annotations(self, frame, detections):
        annotated = frame.copy()
        # Version label, bottom-right, so a swap/rollback is visible on the
        # live feed itself, not just in logs.
        h, w = annotated.shape[:2]
        label = self.code_version
        font, scale, thickness = cv2.FONT_HERSHEY_SIMPLEX, 0.45, 1
        (text_w, text_h), _ = cv2.getTextSize(label, font, scale, thickness)
        pos = (w - text_w - 10, h - 10)
        cv2.putText(annotated, label, pos, font, scale, (0, 0, 0), thickness + 2,
                    cv2.LINE_AA)
        cv2.putText(annotated, label, pos, font, scale, (255, 255, 255), thickness,
                    cv2.LINE_AA)
        for x1, y1, x2, y2, class_id, score in detections:
            p1, p2 = (int(x1), int(y1)), (int(x2), int(y2))
            cv2.rectangle(annotated, p1, p2, (0, 0, 255), 2)
            label = f'{COCO_CLASSES[class_id]} {score:.2f}'
            cv2.putText(annotated, label, (p1[0], max(p1[1] - 8, 0)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        return annotated

    def _publish_annotated(self, frame, detections, header):
        annotated = self._draw_annotations(frame, detections)
        out = self.bridge.cv2_to_imgmsg(annotated, encoding='bgr8')
        out.header = header
        self.annotated_pub.publish(out)

    def on_take_snapshot(self, request, response):
        """Freezes the most recent frame + detections to disk as a JPEG
        plus a JSON metadata sidecar (see scripts/build_lerobot_dataset.py).
        """
        if self.last_frame is None:
            response.success = False
            response.message = 'No frame captured yet'
            return response

        os.makedirs(self.snapshot_dir, exist_ok=True)
        now = datetime.now(timezone.utc)
        stamp = now.strftime('%Y%m%dT%H%M%S%fZ')
        image_path = os.path.join(self.snapshot_dir, f'snapshot_{stamp}.jpg')
        metadata_path = os.path.join(self.snapshot_dir, f'snapshot_{stamp}.json')

        annotated = self._draw_annotations(self.last_frame, self.last_detections)
        cv2.imwrite(image_path, annotated)

        metadata = {
            'codebase_version': self.code_version,
            'capture_timestamp_utc': now.isoformat(),
            'frame_id': self.last_header.frame_id,
            'image_shape': [int(d) for d in self.last_frame.shape],
            'model': {
                'name': 'yolov8n',
                'resolution': self.resolution,
                'file': f'yolov8n_{self.resolution}.onnx',
                'sha256': self.model_sha256,
                'onnxruntime_version': ort.__version__,
                'conf_threshold': self.conf_threshold,
                'iou_threshold': self.iou_threshold,
            },
            'detections': [
                {
                    'class_name': COCO_CLASSES[class_id],
                    'score': float(score),
                    'bbox_xyxy': [float(x1), float(y1), float(x2), float(y2)],
                }
                for x1, y1, x2, y2, class_id, score in self.last_detections
            ],
        }
        with open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2)

        response.success = True
        response.message = f'Saved {image_path} and {metadata_path}'
        self.get_logger().info(response.message)
        return response

    def _log_stats(self, inference_s):
        self.frame_count += 1
        self.inference_time_total += inference_s
        if self.frame_count % LOG_INTERVAL_FRAMES == 0:
            elapsed = time.monotonic() - self.window_start
            fps = LOG_INTERVAL_FRAMES / elapsed if elapsed > 0 else 0.0
            avg_latency_ms = (self.inference_time_total / LOG_INTERVAL_FRAMES) * 1000
            rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
            self.get_logger().info(
                f'fps={fps:.1f} avg_inference_latency_ms={avg_latency_ms:.1f} '
                f'peak_rss_mb={rss_mb:.1f}')
            self.window_start = time.monotonic()
            self.inference_time_total = 0.0


def main():
    rclpy.init()
    node = ObjectDetectorNode()
    try:
        rclpy.spin(node)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
