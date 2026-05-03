"""
GoGoPi MLX90640 thermal camera lifecycle node.

Publishes MLX90640 thermal frames as a 32FC1 sensor_msgs/Image on
/gogopi/thermal/image_raw.
"""

import struct
import time

import rclpy
from rclpy.lifecycle import LifecycleNode, TransitionCallbackReturn, State
from sensor_msgs.msg import Image


class ThermalNode(LifecycleNode):
    def __init__(self):
        super().__init__("thermal_node")
        self.declare_parameter("frame_id", "thermal_camera_link")
        self.declare_parameter("refresh_hz", 4.0)
        self.declare_parameter("image_width", 32)
        self.declare_parameter("image_height", 24)
        self.declare_parameter("i2c_frequency", 400000)
        self.declare_parameter("refresh_rate", "REFRESH_8_HZ")
        self.declare_parameter("device_address", 0x33)

        self._mlx = None
        self._i2c = None
        self._timer = None
        self._pub_image = None
        self._frame = None

    def on_configure(self, state: State) -> TransitionCallbackReturn:
        try:
            import board
            import busio
            import adafruit_mlx90640
        except Exception as exc:
            self.get_logger().error(f"MLX90640 imports failed: {exc}")
            return TransitionCallbackReturn.FAILURE

        width = int(self.get_parameter("image_width").value)
        height = int(self.get_parameter("image_height").value)
        frequency = int(self.get_parameter("i2c_frequency").value)
        address = int(self.get_parameter("device_address").value)
        refresh_rate = self.get_parameter("refresh_rate").value

        self._frame = [0.0] * (width * height)

        try:
            self._i2c = busio.I2C(board.SCL, board.SDA, frequency=frequency)
            self._mlx = adafruit_mlx90640.MLX90640(self._i2c, address)
            if hasattr(adafruit_mlx90640.RefreshRate, refresh_rate):
                setattr(self._mlx, "refresh_rate", getattr(adafruit_mlx90640.RefreshRate, refresh_rate))
            else:
                self.get_logger().warning(
                    "Unknown refresh_rate %s, using REFRESH_8_HZ",
                    refresh_rate,
                )
                self._mlx.refresh_rate = adafruit_mlx90640.RefreshRate.REFRESH_8_HZ
        except Exception as exc:
            self.get_logger().error(f"MLX90640 init failed: {exc}")
            self._i2c = None
            self._mlx = None
            return TransitionCallbackReturn.FAILURE

        self._pub_image = self.create_publisher(Image, "/gogopi/thermal/image_raw", 10)
        self.get_logger().info("Thermal camera configured")
        return TransitionCallbackReturn.SUCCESS

    def on_activate(self, state: State) -> TransitionCallbackReturn:
        hz = float(self.get_parameter("refresh_hz").value)
        self._timer = self.create_timer(1.0 / hz, self._publish_thermal_image)
        self.get_logger().info("Thermal camera active")
        return super().on_activate(state)

    def on_deactivate(self, state: State) -> TransitionCallbackReturn:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        return super().on_deactivate(state)

    def on_cleanup(self, state: State) -> TransitionCallbackReturn:
        if self._i2c is not None:
            try:
                self._i2c.deinit()
            except Exception:
                pass
            self._i2c = None
        self._mlx = None
        self._frame = None
        self._pub_image = None
        return TransitionCallbackReturn.SUCCESS

    def on_shutdown(self, state: State) -> TransitionCallbackReturn:
        if self._i2c is not None:
            try:
                self._i2c.deinit()
            except Exception:
                pass
            self._i2c = None
        self._mlx = None
        return TransitionCallbackReturn.SUCCESS

    def _publish_thermal_image(self):
        if self._mlx is None or self._pub_image is None:
            return

        try:
            self._mlx.getFrame(self._frame)
        except Exception as exc:
            self.get_logger().warning(f"Thermal frame capture failed: {exc}")
            return

        width = int(self.get_parameter("image_width").value)
        height = int(self.get_parameter("image_height").value)

        msg = Image()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self.get_parameter("frame_id").value
        msg.height = height
        msg.width = width
        msg.encoding = "32FC1"
        msg.is_bigendian = 0
        msg.step = width * 4
        msg.data = b"".join(struct.pack("<f", float(v)) for v in self._frame)
        self._pub_image.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = ThermalNode()
    executor = rclpy.executors.SingleThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
