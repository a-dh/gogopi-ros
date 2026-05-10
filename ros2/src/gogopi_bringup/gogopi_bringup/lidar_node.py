"""
GoGoPi YDLidar TX lifecycle node.

Runs a background thread with the YDLidar SDK's blocking scan loop.
Completed scans are queued and published on the ROS2 executor thread
via a timer, keeping thread-safety clean.

Lifecycle transitions:
  configure  → resolve USB hub/port, power on, open device, configure SDK, create publisher
  activate   → start scan thread and drain timer (re-init SDK after power-cycle if needed)
  deactivate → stop scan thread, turn off motor, disconnect SDK, cut USB power
  cleanup / shutdown → disconnect device
"""

import math
import os
import queue
import re
import subprocess
import sys
import threading
import time

# YDLidar SDK installs its Python bindings here (cmake install, not pip)
_YDLIDAR_DIST = "/usr/local/lib/python3/dist-packages"
if _YDLIDAR_DIST not in sys.path:
    sys.path.append(_YDLIDAR_DIST)

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.lifecycle import LifecycleNode, TransitionCallbackReturn, State
from sensor_msgs.msg import LaserScan


class LidarNode(LifecycleNode):
    def __init__(self):
        super().__init__("lidar_node")
        self.declare_parameter("port", "/dev/ydlidar")
        self.declare_parameter("baudrate", 115200)
        self.declare_parameter("frame_id", "lidar_link")
        self.declare_parameter("scan_hz", 7.0)
        self.declare_parameter("range_min", 0.1)
        self.declare_parameter("range_max", 8.0)
        self.declare_parameter("single_channel", True)
        self.declare_parameter("motor_dtr_ctrl", True)

        self._laser = None
        self._ydlidar = None
        self._usb_hub: str | None = None   # uhubctl -l value, e.g. "1-1"
        self._usb_port: int | None = None  # uhubctl -p value, e.g. 4
        self._usb_off = False              # True when we cut power via uhubctl
        self._thread = None
        self._running = False
        self._scan_queue: queue.Queue = queue.Queue(maxsize=1)
        self._timer = None
        self._pub_scan = None

    # ── USB power control ──────────────────────────────────────────────────

    @staticmethod
    def _find_usb_hub_port(dev: str) -> tuple[str | None, int | None]:
        """Derive uhubctl hub location and port number from the device's udev path.

        Parses the USB device component (e.g. "1-1.4") out of the kernel devpath
        returned by udevadm, then splits it into hub ("1-1") and port (4).
        """
        try:
            real = os.path.realpath(dev)
            r = subprocess.run(
                ["udevadm", "info", "--query=path", "--name", real],
                capture_output=True, text=True, timeout=5,
            )
            devpath = r.stdout.strip()
            # devpath: /devices/.../usb1/1-1/1-1.4/1-1.4:1.0/ttyUSB0/tty/ttyUSB0
            m = re.search(r"/(\d+-[\d.]+)/\1:", devpath)
            if not m:
                return None, None
            usb_dev = m.group(1)            # e.g. "1-1.4"
            dot = usb_dev.rfind(".")
            if dot == -1:
                return None, None
            return usb_dev[:dot], int(usb_dev[dot + 1:])   # ("1-1", 4)
        except Exception:
            return None, None

    def _usb_power(self, action: str) -> bool:
        """Switch the lidar's USB port on or off via uhubctl. action: 'on' or 'off'."""
        if self._usb_hub is None:
            return False
        try:
            r = subprocess.run(
                [
                    "sudo", "uhubctl",
                    "-l", self._usb_hub,
                    "-p", str(self._usb_port),
                    "-a", action,
                ],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode != 0:
                self.get_logger().warn(f"uhubctl {action} failed: {r.stderr.strip()}")
                return False
            self._usb_off = (action == "off")
            self.get_logger().info(
                f"USB hub {self._usb_hub} port {self._usb_port} powered {action}"
            )
            return True
        except Exception as exc:
            self.get_logger().warn(f"uhubctl {action} error: {exc}")
            return False

    # ── SDK init helper ────────────────────────────────────────────────────

    def _init_laser(self) -> bool:
        """Create a fresh CYdLidar, apply all options, and call initialize()."""
        ydlidar = self._ydlidar
        self._laser = ydlidar.CYdLidar()

        port    = self.get_parameter("port").value
        baud    = self.get_parameter("baudrate").value
        scan_hz = self.get_parameter("scan_hz").value
        rng_min = self.get_parameter("range_min").value
        rng_max = self.get_parameter("range_max").value

        self._laser.setlidaropt(ydlidar.LidarPropSerialPort,            port)
        self._laser.setlidaropt(ydlidar.LidarPropSerialBaudrate,         baud)
        self._laser.setlidaropt(ydlidar.LidarPropLidarType,              ydlidar.TYPE_TOF)
        self._laser.setlidaropt(ydlidar.LidarPropDeviceType,             ydlidar.YDLIDAR_TYPE_SERIAL)
        self._laser.setlidaropt(ydlidar.LidarPropScanFrequency,          scan_hz)
        self._laser.setlidaropt(ydlidar.LidarPropSampleRate,             4)
        self._laser.setlidaropt(ydlidar.LidarPropMaxRange,               rng_max)
        self._laser.setlidaropt(ydlidar.LidarPropMinRange,               rng_min)
        self._laser.setlidaropt(ydlidar.LidarPropMaxAngle,               180.0)
        self._laser.setlidaropt(ydlidar.LidarPropMinAngle,              -180.0)
        self._laser.setlidaropt(ydlidar.LidarPropSingleChannel,          self.get_parameter("single_channel").value)
        self._laser.setlidaropt(ydlidar.LidarPropSupportMotorDtrCtrl,    self.get_parameter("motor_dtr_ctrl").value)

        if not self._laser.initialize():
            self.get_logger().error("YDLidar initialize() failed — check port and power")
            self._laser = None
            return False
        return True

    # ── lifecycle callbacks ────────────────────────────────────────────────

    def on_configure(self, state: State) -> TransitionCallbackReturn:
        try:
            import ydlidar
            self._ydlidar = ydlidar
            ydlidar.os_init()

            port = self.get_parameter("port").value

            # Resolve USB hub/port once; preserved across cleanup/re-configure cycles.
            if self._usb_hub is None:
                self._usb_hub, self._usb_port = self._find_usb_hub_port(port)
                if self._usb_hub is None:
                    self.get_logger().warn(
                        "Could not resolve YDLidar USB hub/port — uhubctl will be skipped"
                    )

            # Power on is idempotent; only wait for re-enumeration if we cut power earlier.
            was_off = self._usb_off
            self._usb_power("on")
            if was_off:
                time.sleep(2.0)

            if not self._init_laser():
                return TransitionCallbackReturn.FAILURE

        except ImportError:
            self.get_logger().error("ydlidar package not installed — pip install ydlidar")
            return TransitionCallbackReturn.FAILURE
        except Exception as exc:
            self.get_logger().error(f"YDLidar configure failed: {exc}")
            return TransitionCallbackReturn.FAILURE

        self._pub_scan = self.create_publisher(LaserScan, "/gogopi/lidar/scan", 10)
        self.get_logger().info(
            f"YDLidar configured on {port} (USB hub {self._usb_hub} port {self._usb_port})"
        )
        return TransitionCallbackReturn.SUCCESS

    def on_activate(self, state: State) -> TransitionCallbackReturn:
        if self._laser is None:
            # Re-activating after a deactivation that cut USB power.
            self._usb_power("on")
            time.sleep(2.0)     # wait for USB re-enumeration and udev symlink
            if not self._init_laser():
                return TransitionCallbackReturn.FAILURE

        if not self._laser.turnOn():
            self.get_logger().error("YDLidar turnOn() failed")
            return TransitionCallbackReturn.FAILURE

        self._running = True
        self._thread = threading.Thread(target=self._scan_loop, daemon=True)
        self._thread.start()

        scan_hz = self.get_parameter("scan_hz").value
        self._timer = self.create_timer(0.5 / scan_hz, self._publish_from_queue)

        self.get_logger().info("YDLidar active")
        return super().on_activate(state)

    def on_deactivate(self, state: State) -> TransitionCallbackReturn:
        self._running = False
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if self._laser:
            self._laser.turnOff()
        if self._thread:
            self._thread.join(timeout=3.0)
            self._thread = None
        self._disconnect()      # release serial port before cutting power
        self._usb_power("off")
        return super().on_deactivate(state)

    def on_cleanup(self, state: State) -> TransitionCallbackReturn:
        self._disconnect()
        return TransitionCallbackReturn.SUCCESS

    def on_shutdown(self, state: State) -> TransitionCallbackReturn:
        self._disconnect()
        return TransitionCallbackReturn.SUCCESS

    def _disconnect(self):
        if self._laser:
            try:
                self._laser.disconnecting()
            except Exception:
                pass
            self._laser = None

    # ── background scan thread ─────────────────────────────────────────────

    def _scan_loop(self):
        scan = self._ydlidar.LaserScan()
        while self._running and self._ydlidar.os_isOk():
            if not self._laser.doProcessSimple(scan):
                continue
            try:
                self._scan_queue.put_nowait([(p.angle, p.range) for p in scan.points])
            except queue.Full:
                pass

    # ── timer callback (executor thread) ──────────────────────────────────

    def _publish_from_queue(self):
        try:
            points = self._scan_queue.get_nowait()
        except queue.Empty:
            return

        range_min = self.get_parameter("range_min").value
        range_max = self.get_parameter("range_max").value
        scan_hz   = self.get_parameter("scan_hz").value

        # 1°-resolution uniform grid over −π … +π (360 bins)
        n         = 360
        angle_inc = math.radians(1.0)
        angle_min = -math.pi
        ranges    = [float("inf")] * n

        for angle, r in points:
            if not (range_min <= r <= range_max):
                continue
            idx = int((angle - angle_min) / angle_inc) % n
            if r < ranges[idx]:
                ranges[idx] = r

        msg = LaserScan()
        msg.header.stamp    = self.get_clock().now().to_msg()
        msg.header.frame_id = self.get_parameter("frame_id").value
        msg.angle_min       = angle_min
        msg.angle_max       = math.pi - angle_inc
        msg.angle_increment = angle_inc
        msg.scan_time       = 1.0 / scan_hz
        msg.time_increment  = msg.scan_time / n
        msg.range_min       = range_min
        msg.range_max       = range_max
        msg.ranges          = ranges
        self._pub_scan.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = LidarNode()
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
