"""
GoGoPi USB camera lifecycle node.

Publishes raw camera frames as sensor_msgs/Image on /gogopi/camera/image_raw.
"""

import cv2
import numpy as np
import rclpy
from rclpy.lifecycle import LifecycleNode, TransitionCallbackReturn, State
from sensor_msgs.msg import Image


class CameraNode(LifecycleNode):
    def __init__(self):
        super().__init__("camera_node")
        self.declare_parameter("camera_device", "/dev/video0")
        self.declare_parameter("frame_id", "camera_link")
        self.declare_parameter("image_width", 640)
        self.declare_parameter("image_height", 480)
        self.declare_parameter("image_hz", 15.0)

        self._capture = None
        self._timer = None
        self._pub_image = None

    def on_configure(self, state: State) -> TransitionCallbackReturn:
        device = self.get_parameter("camera_device").value
        width = int(self.get_parameter("image_width").value)
        height = int(self.get_parameter("image_height").value)

        self._capture = cv2.VideoCapture(device, cv2.CAP_ANY)
        self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        if not self._capture.isOpened():
            self.get_logger().error(f"Failed to open camera device: {device}")
            self._capture.release()
            self._capture = None
            return TransitionCallbackReturn.FAILURE

        self._pub_image = self.create_publisher(Image, "/gogopi/camera/image_raw", 10)
        self.get_logger().info(f"Camera configured on {device} ({width}x{height})")
        return TransitionCallbackReturn.SUCCESS

    def on_activate(self, state: State) -> TransitionCallbackReturn:
        hz = float(self.get_parameter("image_hz").value)
        self._timer = self.create_timer(1.0 / hz, self._publish_image)
        self.get_logger().info("Camera active")
        return super().on_activate(state)

    def on_deactivate(self, state: State) -> TransitionCallbackReturn:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        return super().on_deactivate(state)

    def on_cleanup(self, state: State) -> TransitionCallbackReturn:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        self._pub_image = None
        return TransitionCallbackReturn.SUCCESS

    def on_shutdown(self, state: State) -> TransitionCallbackReturn:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        return TransitionCallbackReturn.SUCCESS

    def _publish_image(self):
        if self._capture is None:
            return

        ret, frame = self._capture.read()
        if not ret or frame is None:
            self.get_logger().warning("Camera read failed")
            return

        height, width, channels = frame.shape
        if channels != 3:
            self.get_logger().warning("Camera frame has unsupported number of channels")
            return

        msg = Image()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self.get_parameter("frame_id").value
        msg.height = height
        msg.width = width
        msg.encoding = "bgr8"
        msg.is_bigendian = 0
        msg.step = width * 3
        msg.data = frame.tobytes()
        self._pub_image.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = CameraNode()
    executor = rclpy.executors.SingleThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
