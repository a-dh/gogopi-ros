"""
GoGoPi board lifecycle node.

Lifecycle transitions:
  configure  → Board.initialize(), create pub/sub/srv
  activate   → start publish timers
  deactivate → cancel timers, stop motors
  cleanup    → Board.cleanup()
  shutdown   → stop motors, Board.cleanup()
"""

import math

import rclpy
from rclpy.executors import ExternalShutdownException
from rclpy.lifecycle import LifecycleNode, TransitionCallbackReturn, State

from geometry_msgs.msg import Twist
from gogopi_msgs.msg import PixelArray, ServoCommand
from gogopi_msgs.srv import SetServo
from sensor_msgs.msg import Joy, JointState
from std_msgs.msg import Bool, Float32, Float32MultiArray, Int16MultiArray, UInt16
from std_srvs.srv import SetBool, Trigger


class BoardNode(LifecycleNode):
    def __init__(self):
        super().__init__("board_node")
        self.declare_parameter("wheel_separation_m", 0.12)
        self.declare_parameter("max_speed_pct", 80)
        self.declare_parameter("motor_left_id", 1)
        self.declare_parameter("motor_right_id", 2)
        self.declare_parameter("button_poll_hz", 50.0)
        self.declare_parameter("battery_hz", 2.0)
        self.declare_parameter("state_hz", 10.0)
        self.declare_parameter("servo_angle_offset_deg", [0.0, 0.0])  # offsets for servo 1 and 2
        # Mechanical travel limits (servo degrees, after offset) for servo 1 (tilt) and 2 (pan).
        # Tilt (measured with tools/pt_range_check.py): hard stop ~6°, clean, no strain; a cable
        # starts touching at ~14° and drags the head down to the stop. 17° keeps clear of the
        # cable — drop to 9° once the cable is rerouted and a sweep shows smooth steps to 6°.
        # Tilt moves freely up to the servo's 180°, but above 90° (camera 45° up) panning
        # far binds the cables, so 90° is the practical max.
        self.declare_parameter("servo_min_deg", [17.0, 0.0])
        self.declare_parameter("servo_max_deg", [90.0, 180.0])

        self._timers = []
        self._Board = None
        self._active = False
        self._last_servo_angle_deg: dict[int, float] = {}

    # ── lifecycle callbacks ────────────────────────────────────────────────

    def on_configure(self, state: State) -> TransitionCallbackReturn:
        try:
            import HiwonderSDK.Board as Board
        except Exception:
            self.get_logger().error("HiwonderSDK import failed — install with: pip3 install --user --break-system-packages -e <repo>")
            return TransitionCallbackReturn.FAILURE

        try:
            Board.initialize()
        except Exception as exc:
            self.get_logger().error(f"Board.initialize() failed: {exc}")
            return TransitionCallbackReturn.FAILURE

        self._Board = Board

        # publishers
        self._pub_battery = self.create_publisher(UInt16, "/gogopi/battery_raw", 10)
        self._pub_battery_voltage = self.create_publisher(Float32, "/gogopi/battery_voltage", 10)
        self._pub_buttons = self.create_publisher(Joy, "/gogopi/buttons", 10)
        self._pub_servo_state = self.create_publisher(JointState, "/gogopi/servo_state", 10)
        self._pub_motor_state = self.create_publisher(Float32MultiArray, "/gogopi/motor_state", 10)

        # subscribers
        self.create_subscription(Twist, "/gogopi/cmd_vel", self._cb_cmd_vel, 10)
        self.create_subscription(Int16MultiArray, "/gogopi/motors_raw", self._cb_motors_raw, 10)
        self.create_subscription(ServoCommand, "/gogopi/servo_cmd", self._cb_servo_cmd, 10)
        self.create_subscription(PixelArray, "/gogopi/board_pixels", self._cb_board_pixels, 10)
        self.create_subscription(Bool, "/gogopi/buzzer", self._cb_buzzer, 10)
        self.create_subscription(Bool, "/gogopi/led1", self._cb_led1, 10)
        self.create_subscription(Bool, "/gogopi/led2", self._cb_led2, 10)
        self.create_subscription(JointState, "/joint_states", self._cb_joint_states, 10)

        # services
        self.create_service(SetServo, "/gogopi/set_servo", self._svc_set_servo)
        self.create_service(SetBool, "/gogopi/set_buzzer", self._svc_set_buzzer)
        self.create_service(Trigger, "/gogopi/e_stop", self._svc_e_stop)
        self.create_service(Trigger, "/gogopi/enter_power_save", self._svc_enter_power_save)
        self.create_service(Trigger, "/gogopi/exit_power_save", self._svc_exit_power_save)

        self.get_logger().info("Board configured")
        return TransitionCallbackReturn.SUCCESS

    def on_activate(self, state: State) -> TransitionCallbackReturn:
        def hz(param):
            return 1.0 / self.get_parameter(param).value

        self._timers = [
            self.create_timer(hz("battery_hz"), self._publish_battery),
            self.create_timer(hz("button_poll_hz"), self._publish_buttons),
            self.create_timer(hz("state_hz"), self._publish_state),
        ]
        self._active = True
        self.get_logger().info("Board active")
        return super().on_activate(state)

    def on_deactivate(self, state: State) -> TransitionCallbackReturn:
        for t in self._timers:
            t.cancel()
        self._timers.clear()
        self._active = False
        self._Board.setMotor(1, 0)
        self._Board.setMotor(2, 0)
        self.get_logger().info("Board deactivated")
        return super().on_deactivate(state)

    def on_cleanup(self, state: State) -> TransitionCallbackReturn:
        self._Board.cleanup()
        self._Board = None
        return TransitionCallbackReturn.SUCCESS

    def on_shutdown(self, state: State) -> TransitionCallbackReturn:
        if self._Board:
            self._Board.setMotor(1, 0)
            self._Board.setMotor(2, 0)
            self._Board.cleanup()
            self._Board = None
        return TransitionCallbackReturn.SUCCESS

    # ── publish callbacks ──────────────────────────────────────────────────

    def _publish_battery(self):
        raw = self._Board.getBattery()
        msg = UInt16()
        msg.data = raw
        self._pub_battery.publish(msg)
        v_msg = Float32()
        v_msg.data = raw / 1000.0
        self._pub_battery_voltage.publish(v_msg)

    def _publish_buttons(self):
        msg = Joy()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.buttons = [int(self._Board.getKey1()), int(self._Board.getKey2())]
        self._pub_buttons.publish(msg)

    def _publish_state(self):
        # motor state
        m_msg = Float32MultiArray()
        m_msg.data = [float(self._Board.getMotor(i)) for i in range(1, 5)]
        self._pub_motor_state.publish(m_msg)

        # servo state using URDF joint names so robot_state_publisher can consume it
        s_msg = JointState()
        s_msg.header.stamp = self.get_clock().now().to_msg()
        s_msg.name = ["tilt_joint", "pan_joint"]
        s_msg.position = [
            math.radians(90.0 - self._Board.getPWMServoAngle(1)),
            math.radians(self._Board.getPWMServoAngle(2) - 90.0),
        ]
        self._pub_servo_state.publish(s_msg)

    # ── subscriber callbacks ───────────────────────────────────────────────

    def _cb_cmd_vel(self, msg: Twist):
        sep = self.get_parameter("wheel_separation_m").value
        max_spd = self.get_parameter("max_speed_pct").value
        left_id = self.get_parameter("motor_left_id").value
        right_id = self.get_parameter("motor_right_id").value

        # differential drive: v ± ω*d/2, scaled to [-100, 100]
        v = msg.linear.x
        w = msg.angular.z
        left = v - w * sep / 2.0
        right = v + w * sep / 2.0
        scale = max_spd / max(abs(left), abs(right), 1e-6)
        scale = min(scale, max_spd)
        self._Board.setMotor(left_id, int(left * scale))
        self._Board.setMotor(right_id, int(right * scale))

    def _cb_motors_raw(self, msg: Int16MultiArray):
        for i, spd in enumerate(msg.data[:4], start=1):
            self._Board.setMotor(i, int(spd))

    def _cb_servo_cmd(self, msg: ServoCommand):
        self._apply_servo(msg)

    def _cb_board_pixels(self, msg: PixelArray):
        for idx, color in zip(msg.index, msg.color):
            self._Board.setPixelColor(
                int(idx),
                int(color.r * 255),
                int(color.g * 255),
                int(color.b * 255),
            )
        self._Board.show()

    def _cb_buzzer(self, msg: Bool):
        self._Board.setBuzzer(1 if msg.data else 0)

    def _cb_led1(self, msg: Bool):
        from HiwonderSDK.Board import LEDState
        self._Board.setLed1(LEDState.ON if msg.data else LEDState.OFF)

    def _cb_led2(self, msg: Bool):
        from HiwonderSDK.Board import LEDState
        self._Board.setLed2(LEDState.ON if msg.data else LEDState.OFF)

    def _cb_joint_states(self, msg: JointState):
        if not self._active:
            return
        # Inverse of _publish_state: tilt pos = radians(90-angle), pan pos = radians(angle-90)
        joint_to_servo = {
            'tilt_joint': (1, lambda p: 90.0 - math.degrees(p)),
            'pan_joint':  (2, lambda p: 90.0 + math.degrees(p)),
        }
        for name, pos in zip(msg.name, msg.position):
            if name not in joint_to_servo:
                continue
            sid, to_angle = joint_to_servo[name]
            angle = to_angle(pos)
            last = self._last_servo_angle_deg.get(sid)
            if last is not None and abs(angle - last) < 1.0:
                continue
            self._last_servo_angle_deg[sid] = angle
            cmd = ServoCommand()
            cmd.servo_id = sid
            cmd.use_pulse = False
            cmd.angle_deg = angle
            cmd.move_time_ms = 0
            self._apply_servo(cmd)

    # ── service callbacks ──────────────────────────────────────────────────

    def _servo_limits(self, servo_id: int) -> tuple[float, float]:
        lo = self.get_parameter("servo_min_deg").value
        hi = self.get_parameter("servo_max_deg").value
        i = servo_id - 1
        return (lo[i] if i < len(lo) else 0.0, hi[i] if i < len(hi) else 180.0)

    def _clamp_servo(self, servo_id: int, angle: float) -> float:
        lo, hi = self._servo_limits(servo_id)
        clamped = min(max(angle, lo), hi)
        if clamped != angle:
            self.get_logger().warning(
                f"Servo {servo_id}: {angle:.1f}° outside travel {lo:.0f}–{hi:.0f}°, using {clamped:.1f}°",
                throttle_duration_sec=5.0)
        return clamped

    def _apply_servo(self, cmd: ServoCommand):
        try:
            if cmd.use_pulse:
                # 500–2500 µs ↔ 0–180°; clamp in angle space so the same limits apply
                angle = self._clamp_servo(cmd.servo_id, (cmd.pulse_us - 500) * 180.0 / 2000.0)
                pulse = int(round(500 + angle * 2000.0 / 180.0))
                self._Board.setPWMServoPulse(cmd.servo_id, pulse, cmd.move_time_ms)
            else:
                offsets = self.get_parameter("servo_angle_offset_deg").value
                offset = offsets[cmd.servo_id - 1] if cmd.servo_id <= len(offsets) else 0.0
                adjusted_angle = self._clamp_servo(cmd.servo_id, cmd.angle_deg + offset)
                self._Board.setPWMServoAngle(cmd.servo_id, adjusted_angle)
        except Exception as exc:
            self.get_logger().error(f"Servo {cmd.servo_id} I2C error: {exc}", throttle_duration_sec=5.0)

    def _svc_set_servo(self, request, response):
        # _apply_servo logs and suppresses hardware errors; check outcome via servo state
        self._apply_servo(request.command)
        response.success = True
        return response

    def _svc_set_buzzer(self, request, response):
        self._Board.setBuzzer(1 if request.data else 0)
        response.success = True
        return response

    def _svc_e_stop(self, request, response):
        for i in range(1, 5):
            self._Board.setMotor(i, 0)
        # Disable servos by setting to neutral position (power saving)
        for i in range(1, 3):  # Assuming servos 1 and 2 for pan-tilt
            try:
                self._Board.setPWMServoPulse(i, 0, 0)  # Attempt to disable PWM
            except:
                pass  # If not supported, ignore
        response.success = True
        response.message = "All motors stopped, servos disabled"
        return response

    def _svc_enter_power_save(self, request, response):
        # Cancel timers to stop publishing
        for t in self._timers:
            t.cancel()
        self._timers.clear()
        # Stop motors
        for i in range(1, 5):
            self._Board.setMotor(i, 0)
        # Disable servos
        for i in range(1, 3):
            try:
                self._Board.setPWMServoPulse(i, 0, 0)
            except:
                pass
        # Turn off buzzer and LEDs
        self._Board.setBuzzer(0)
        from HiwonderSDK.Board import LEDState
        self._Board.setLed1(LEDState.OFF)
        self._Board.setLed2(LEDState.OFF)
        response.success = True
        response.message = "Entered power save mode: timers stopped, motors/servos disabled, outputs off"
        return response

    def _svc_exit_power_save(self, request, response):
        # Restart timers
        def hz(param):
            return 1.0 / self.get_parameter(param).value

        self._timers = [
            self.create_timer(hz("battery_hz"), self._publish_battery),
            self.create_timer(hz("button_poll_hz"), self._publish_buttons),
            self.create_timer(hz("state_hz"), self._publish_state),
        ]
        response.success = True
        response.message = "Exited power save mode: timers restarted"
        return response


def main(args=None):
    rclpy.init(args=args)
    node = BoardNode()
    executor = rclpy.executors.SingleThreadedExecutor()
    executor.add_node(node)
    try:
        executor.spin()
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    except Exception as exc:
        node.get_logger().error(f"Executor error: {exc}")
    finally:
        node.on_shutdown(None)  # stop motors and release GPIO before the process exits
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
