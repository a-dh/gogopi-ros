"""
GoGoPi sonar lifecycle node.

Publishes ultrasonic range readings and controls the two NeoPixels
on the Sonar module (I2C addr 0x77, smbus2).

Lifecycle transitions:
  configure  → create Sonar instance, pub/sub
  activate   → start range timer
  deactivate → cancel timer
  cleanup / shutdown → no hardware resources to release (smbus2 opens/closes per call)
"""

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.lifecycle import LifecycleNode, TransitionCallbackReturn, State

from gogopi_msgs.msg import PixelArray
from sensor_msgs.msg import Range
from std_msgs.msg import UInt8


class SonarNode(LifecycleNode):
    def __init__(self):
        super().__init__("sonar_node")
        self.declare_parameter("range_hz", 10.0)
        self.declare_parameter("frame_id", "sonar_link")
        self.declare_parameter("field_of_view_rad", 0.26)

        self._sonar = None
        self._timer = None

    # ── lifecycle callbacks ────────────────────────────────────────────────

    def on_configure(self, state: State) -> TransitionCallbackReturn:
        from HiwonderSDK.Sonar import Sonar

        try:
            self._sonar = Sonar()
            self._sonar.setRGBMode(0)  # manual colour control
        except Exception as exc:
            self.get_logger().error(f"Sonar init failed: {exc}")
            return TransitionCallbackReturn.FAILURE

        self._pub_range = self.create_publisher(Range, "/gogopi/sonar/range", 10)

        self.create_subscription(PixelArray, "/gogopi/sonar/pixels", self._cb_pixels, 10)
        self.create_subscription(UInt8, "/gogopi/sonar/rgb_mode", self._cb_rgb_mode, 10)

        self.get_logger().info("Sonar configured")
        return TransitionCallbackReturn.SUCCESS

    def on_activate(self, state: State) -> TransitionCallbackReturn:
        hz = self.get_parameter("range_hz").value
        self._timer = self.create_timer(1.0 / hz, self._publish_range)
        self.get_logger().info("Sonar active")
        return super().on_activate(state)

    def on_deactivate(self, state: State) -> TransitionCallbackReturn:
        if self._timer:
            self._timer.cancel()
            self._timer = None
        return super().on_deactivate(state)

    def on_cleanup(self, state: State) -> TransitionCallbackReturn:
        self._sonar = None
        return TransitionCallbackReturn.SUCCESS

    def on_shutdown(self, state: State) -> TransitionCallbackReturn:
        self._sonar = None
        return TransitionCallbackReturn.SUCCESS

    # ── publish callbacks ──────────────────────────────────────────────────

    def _publish_range(self):
        try:
            dist_mm = self._sonar.getDistance()
        except Exception as exc:
            self.get_logger().warning(f"Sonar read failed: {exc}")
            return

        msg = Range()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self.get_parameter("frame_id").value
        msg.radiation_type = Range.ULTRASOUND
        msg.field_of_view = self.get_parameter("field_of_view_rad").value
        msg.min_range = 0.02
        msg.max_range = 5.0
        msg.range = dist_mm / 1000.0
        self._pub_range.publish(msg)

    # ── subscriber callbacks ───────────────────────────────────────────────

    def _cb_pixels(self, msg: PixelArray):
        for idx, color in zip(msg.index, msg.color):
            packed = (
                (int(color.r * 255) << 16)
                | (int(color.g * 255) << 8)
                | int(color.b * 255)
            )
            self._sonar.setPixelColor(int(idx), packed)
        self._sonar.show()

    def _cb_rgb_mode(self, msg: UInt8):
        self._sonar.setRGBMode(msg.data)


def main(args=None):
    rclpy.init(args=args)
    node = SonarNode()
    executor = rclpy.executors.SingleThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    except Exception as exc:
        node.get_logger().error(f"Executor error: {exc}")
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
