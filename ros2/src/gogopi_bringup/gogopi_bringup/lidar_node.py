"""
GoGoPi YDLidar TX lifecycle node.

Runs a background thread with the YDLidar SDK's blocking scan loop.
Completed scans are queued and published on the ROS2 executor thread
via a timer, keeping thread-safety clean.

Lifecycle transitions:
  configure  → open device, configure SDK, create publisher
  activate   → start scan thread and drain timer
  deactivate → stop scan thread, turn off motor
  cleanup / shutdown → disconnect device
"""

import math
import queue
import sys
import threading

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
        self.declare_parameter("port", "/dev/ttyUSB0")
        self.declare_parameter("baudrate", 115200)
        self.declare_parameter("frame_id", "lidar_link")
        self.declare_parameter("scan_hz", 7.0)
        self.declare_parameter("range_min", 0.1)
        self.declare_parameter("range_max", 12.0)
        self.declare_parameter("single_channel", True)
        self.declare_parameter("motor_dtr_ctrl", True)

        self._laser = None
        self._ydlidar = None
        self._thread = None
        self._running = False
        self._scan_queue: queue.Queue = queue.Queue(maxsize=1)
        self._timer = None
        self._pub_scan = None

    # ── lifecycle callbacks ────────────────────────────────────────────────

    def on_configure(self, state: State) -> TransitionCallbackReturn:
        try:
            import ydlidar
            self._ydlidar = ydlidar
            ydlidar.os_init()
            self._laser = ydlidar.CYdLidar()

            port    = self.get_parameter("port").value
            baud    = self.get_parameter("baudrate").value
            scan_hz = self.get_parameter("scan_hz").value
            rng_min = self.get_parameter("range_min").value
            rng_max = self.get_parameter("range_max").value

            self._laser.setlidaropt(ydlidar.LidarPropSerialPort,     port)
            self._laser.setlidaropt(ydlidar.LidarPropSerialBaudrate,  baud)
            self._laser.setlidaropt(ydlidar.LidarPropLidarType,       ydlidar.TYPE_TRIANGLE)
            self._laser.setlidaropt(ydlidar.LidarPropDeviceType,      ydlidar.YDLIDAR_TYPE_SERIAL)
            self._laser.setlidaropt(ydlidar.LidarPropScanFrequency,   scan_hz)
            self._laser.setlidaropt(ydlidar.LidarPropSampleRate,      3)
            self._laser.setlidaropt(ydlidar.LidarPropMaxRange,        rng_max)
            self._laser.setlidaropt(ydlidar.LidarPropMinRange,        rng_min)
            self._laser.setlidaropt(ydlidar.LidarPropMaxAngle,        180.0)
            self._laser.setlidaropt(ydlidar.LidarPropMinAngle,       -180.0)
            self._laser.setlidaropt(ydlidar.LidarPropSingleChannel,      self.get_parameter("single_channel").value)
            self._laser.setlidaropt(ydlidar.LidarPropSupportMotorDtrCtrl, self.get_parameter("motor_dtr_ctrl").value)

            if not self._laser.initialize():
                self.get_logger().error("YDLidar initialize() failed — check port and power")
                return TransitionCallbackReturn.FAILURE

        except ImportError:
            self.get_logger().error("ydlidar package not installed — pip install ydlidar")
            return TransitionCallbackReturn.FAILURE
        except Exception as exc:
            self.get_logger().error(f"YDLidar configure failed: {exc}")
            return TransitionCallbackReturn.FAILURE

        self._pub_scan = self.create_publisher(LaserScan, "/gogopi/lidar/scan", 10)
        self.get_logger().info(f"YDLidar configured on {self.get_parameter('port').value}")
        return TransitionCallbackReturn.SUCCESS

    def on_activate(self, state: State) -> TransitionCallbackReturn:
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
