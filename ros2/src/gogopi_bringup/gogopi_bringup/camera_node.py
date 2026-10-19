"""
GoGoPi USB camera lifecycle node.

Publishes raw camera frames as sensor_msgs/Image on /gogopi/camera/image_raw.
If reads keep failing (e.g. the camera dropped off USB and re-enumerated), the
device is released and reopened. Use a /dev/v4l/by-id/ path for camera_device
so the reopen finds it again even if it comes back as a different /dev/videoN.
"""

import time

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.lifecycle import LifecycleNode, TransitionCallbackReturn, State
from sensor_msgs.msg import Image

REOPEN_AFTER_S = 2.0         # no frame for this long → reopen (a failed read can
                             # itself block ~10 s in V4L2 select())
REOPEN_INTERVAL_S = 2.0      # min time between reopen attempts


class CameraNode(LifecycleNode):
    def __init__(self):
        super().__init__("camera_node")
        self.declare_parameter("camera_device", "/dev/video0")
        self.declare_parameter("frame_id", "camera_link")
        self.declare_parameter("image_width", 640)
        self.declare_parameter("image_height", 480)
        self.declare_parameter("image_hz", 15.0)
        self.declare_parameter("image_rotation", 0)  # 0, 90, 180, 270 degrees

        self._capture = None
        self._timer = None
        self._pub_image = None
        self._fail_count = 0
        self._last_reopen = 0.0
        self._last_frame = 0.0

    def on_configure(self, state: State) -> TransitionCallbackReturn:
        global cv2
        try:
            import cv2
        except ImportError as e:
            self.get_logger().error(f"cv2 not available — install python3-opencv: {e}")
            return TransitionCallbackReturn.FAILURE

        if not self._open_capture():
            return TransitionCallbackReturn.FAILURE

        self._pub_image = self.create_publisher(Image, "/gogopi/camera/image_raw", 10)
        self.get_logger().info("Camera configured")
        return TransitionCallbackReturn.SUCCESS

    def _open_capture(self) -> bool:
        device = self.get_parameter("camera_device").value
        width = int(self.get_parameter("image_width").value)
        height = int(self.get_parameter("image_height").value)

        self._capture = cv2.VideoCapture(device, cv2.CAP_V4L2)
        self._capture.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        self._capture.set(cv2.CAP_PROP_FRAME_HEIGHT, height)

        if not self._capture.isOpened():
            self.get_logger().error(f"Failed to open camera device: {device}")
            self._capture.release()
            self._capture = None
            return False
        self.get_logger().info(f"Camera opened: {device} ({width}x{height})")
        self._fail_count = 0
        self._last_frame = time.monotonic()
        return True

    def _try_reopen(self):
        now = time.monotonic()
        if now - self._last_reopen < REOPEN_INTERVAL_S:
            return
        self._last_reopen = now
        self.get_logger().warning("Camera not delivering frames — reopening device")
        if self._capture is not None:
            self._capture.release()
            self._capture = None
        self._open_capture()

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
            self._try_reopen()
            return

        ret, frame = self._capture.read()
        if not ret or frame is None:
            self._fail_count += 1
            if self._fail_count == 1:
                self.get_logger().warning("Camera read failed")
            if time.monotonic() - self._last_frame >= REOPEN_AFTER_S:
                self._try_reopen()
            return
        self._last_frame = time.monotonic()
        if self._fail_count:
            self.get_logger().info(f"Camera frames resumed after {self._fail_count} failed reads")
            self._fail_count = 0

        height, width, channels = frame.shape
        if channels != 3:
            self.get_logger().warning("Camera frame has unsupported number of channels")
            return

        # Apply rotation if specified
        rotation = int(self.get_parameter("image_rotation").value)
        if rotation == 90:
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
            height, width = width, height
        elif rotation == 180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        elif rotation == 270:
            frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
            height, width = width, height

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
        while rclpy.ok():
            try:
                executor.spin()
            except (KeyboardInterrupt, ExternalShutdownException):
                break
            except Exception as exc:
                # e.g. 'activate' requested while unconfigured (sensor missing at start) raises
                # out of rclpy's change_state handler — log it and keep serving instead of exiting
                node.get_logger().error(f"Executor error (continuing): {exc}")
                time.sleep(0.5)
    finally:
        node.on_shutdown(None)  # release camera device before the process exits
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
