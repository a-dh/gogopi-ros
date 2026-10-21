"""
GoGoPi sonar lifecycle node.

Publishes ultrasonic range readings and controls the two NeoPixels
on the Sonar module (I2C addr 0x77, smbus2).

Lifecycle transitions:
  configure  → create Sonar instance, nav lights dimmed (idle), pub/sub
  activate   → start range timer
  deactivate → cancel timer
  cleanup / shutdown → nav lights back to full (smbus2 opens/closes per call, nothing to release)
"""

import time

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.lifecycle import LifecycleNode, TransitionCallbackReturn, State

from gogopi_msgs.msg import PixelArray
from sensor_msgs.msg import Range
from std_msgs.msg import UInt8

# Sonar RGB nav lights: LED 0 = port (red), LED 1 = starboard (green), at their boot
# level (services/hw_sonar_lights.py; 128 matches the hat NeoPixels at full).
# sonar_node dims them (nav_light_idle_brightness) while it holds the sonar.
NAV_LIGHTS = {0: (128, 0, 0), 1: (0, 128, 0)}


class SonarNode(LifecycleNode):
    def __init__(self):
        super().__init__("sonar_node")
        self.declare_parameter("range_hz", 10.0)
        self.declare_parameter("frame_id", "sonar_link")
        self.declare_parameter("field_of_view_rad", 0.26)
        # Shortest reliable reading (m); consumers discard readings below it
        self.declare_parameter("range_min", 0.05)
        # Nav-light brightness (0-1, boot level = 1.0) while ROS holds the sonar but is idle.
        self.declare_parameter("nav_light_idle_brightness", 0.05)

        self._sonar = None
        self._timer = None

    # ── lifecycle callbacks ────────────────────────────────────────────────

    def on_configure(self, state: State) -> TransitionCallbackReturn:
        from HiwonderSDK.Sonar import Sonar

        try:
            self._sonar = Sonar()
            self._sonar.setRGBMode(0)  # manual colour control
            self._show_nav_lights(self.get_parameter("nav_light_idle_brightness").value)
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
        self._restore_nav_lights()
        self._sonar = None
        return TransitionCallbackReturn.SUCCESS

    def on_shutdown(self, state: State) -> TransitionCallbackReturn:
        if self._sonar:
            self._restore_nav_lights()
        self._sonar = None
        return TransitionCallbackReturn.SUCCESS

    def _show_nav_lights(self, brightness: float):
        for idx, (r, g, b) in NAV_LIGHTS.items():
            r, g, b = (round(c * brightness) for c in (r, g, b))
            self._sonar.setPixelColor(idx, (r << 16) | (g << 8) | b)

    def _restore_nav_lights(self):
        try:
            self._sonar.setRGBMode(0)
            self._show_nav_lights(1.0)
        except Exception as exc:
            self.get_logger().warning(f"Sonar nav lights restore failed: {exc}")

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
        msg.min_range = self.get_parameter("range_min").value
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
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
