import sys
import time
from enum import Enum
import pigpio
from led_client import LEDClient

if sys.version_info.major == 2:
    print("Please run this program with python3!")
    sys.exit(0)


class LEDState(Enum):
    """LED state: 0 = on (active low), 1 = off."""
    ON = 0
    OFF = 1


__ADC_BAT_ADDR = 0
__SERVO_ADDR = 21
__MOTOR_ADDR = 31
__SERVO_ADDR_CMD = 40

__motor_speed = [0, 0, 0, 0]
__servo_angle = [0, 0, 0, 0, 0, 0]
__servo_pulse = [0, 0, 0, 0, 0, 0]
__i2c_bus = 1
__i2c_addr = 0x7A

pi = None
_i2c_handle = None
_led_client = None

__RGB_COUNT = 2
__RGB_BRIGHTNESS = 120 / 255.0
_pixel_buffer = [(0, 0, 0)] * __RGB_COUNT

BUZZER_PIN = 6    # BCM GPIO 6  / BOARD pin 31
LED1_PIN = 16     # BCM GPIO 16 / BOARD pin 36
LED2_PIN = 26     # BCM GPIO 26 / BOARD pin 37
KEY1_PIN = 13     # BCM GPIO 13 / BOARD pin 33
KEY2_PIN = 23     # BCM GPIO 23 / BOARD pin 16

_led1_state = LEDState.OFF.value
_led2_state = LEDState.OFF.value


def initialize():
    """Connect to pigpiod and configure all GPIO pins. Call before any other function."""
    global pi, _led_client, _led1_state, _led2_state
    pi = pigpio.pi()
    if not pi.connected:
        raise RuntimeError("Cannot connect to pigpiod — is the daemon running?")
    _led_client = LEDClient()
    show()  # blank NeoPixels
    pi.set_mode(BUZZER_PIN, pigpio.OUTPUT)
    pi.write(BUZZER_PIN, 0)
    _led1_state = LEDState.ON.value
    _led2_state = LEDState.OFF.value
    pi.set_mode(LED1_PIN, pigpio.OUTPUT)
    pi.write(LED1_PIN, _led1_state)
    pi.set_mode(LED2_PIN, pigpio.OUTPUT)
    pi.write(LED2_PIN, _led2_state)
    pi.set_mode(KEY1_PIN, pigpio.INPUT)
    pi.set_mode(KEY2_PIN, pigpio.INPUT)
    pi.set_pull_up_down(KEY1_PIN, pigpio.PUD_UP)
    pi.set_pull_up_down(KEY2_PIN, pigpio.PUD_UP)


def cleanup():
    """Release pigpiod resources. Call when shutting down."""
    global pi, _i2c_handle
    if pi is None:
        return
    if _i2c_handle is not None:
        try:
            pi.i2c_close(_i2c_handle)
        except Exception:
            pass
        _i2c_handle = None
    try:
        pi.stop()
    except Exception:
        pass
    pi = None


def _i2c_open():
    global _i2c_handle
    if _i2c_handle is None:
        _i2c_handle = pi.i2c_open(__i2c_bus, __i2c_addr)
    return _i2c_handle


def _i2c_write(data):
    pi.i2c_write_device(_i2c_open(), data)


def _i2c_write_read(write_data, read_count):
    handle = _i2c_open()
    cmd = [0, __i2c_addr, len(write_data)] + list(write_data) + [1, __i2c_addr, read_count]
    return pi.i2c_zip(handle, cmd)


def setPixelColor(index, r, g, b):
    """Buffer a NeoPixel colour (0-255 per channel). Call show() to transmit."""
    global _pixel_buffer
    if 0 <= index < __RGB_COUNT:
        _pixel_buffer[index] = (r, g, b)


def show():
    """Send pixel buffer to WS281x LEDs via the led-proxy daemon."""
    if _led_client is None:
        return
    for i, (r, g, b) in enumerate(_pixel_buffer):
        _led_client.set_intensity_color(
            f"#{r:02x}{g:02x}{b:02x}", intensity=__RGB_BRIGHTNESS, index=i
        )


def setMotor(index, speed):
    if index < 1 or index > 4:
        raise AttributeError("Invalid motor num: %d" % index)
    index -= 1
    speed = max(-100, min(100, speed))
    speed = -speed
    _i2c_write([__MOTOR_ADDR + index, speed & 0xFF])
    __motor_speed[index] = speed
    return __motor_speed[index]


def getMotor(index):
    if index < 1 or index > 4:
        raise AttributeError("Invalid motor num: %d" % index)
    return __motor_speed[index - 1]


def setPWMServoAngle(servo_id, angle):
    if servo_id < 1 or servo_id > 6:
        raise AttributeError("Invalid Servo ID: %d" % servo_id)
    index = servo_id - 1
    angle = max(0, min(180, round(angle)))
    pulse = max(500, min(2500, int(angle * 2000 / 180 + 500)))
    buf = (
        [__SERVO_ADDR_CMD, 1]
        + list((0).to_bytes(2, "little"))
        + [servo_id]
        + list(pulse.to_bytes(2, "little"))
    )
    _i2c_write(buf)
    __servo_angle[index] = angle
    __servo_pulse[index] = pulse
    return __servo_angle[index]


def setPWMServoPulse(servo_id, pulse=1500, use_time=1000):
    if servo_id < 1 or servo_id > 6:
        raise AttributeError("Invalid Servo ID: %d" % servo_id)
    index = servo_id - 1
    pulse = max(500, min(2500, pulse))
    use_time = max(0, min(30000, use_time))
    buf = (
        [__SERVO_ADDR_CMD, 1]
        + list(use_time.to_bytes(2, "little"))
        + [servo_id]
        + list(pulse.to_bytes(2, "little"))
    )
    _i2c_write(buf)
    __servo_pulse[index] = pulse
    __servo_angle[index] = int((pulse - 500) * 0.09)
    return __servo_pulse[index]


def getPWMServoAngle(servo_id):
    if servo_id < 1 or servo_id > 6:
        raise AttributeError("Invalid Servo ID: %d" % servo_id)
    return __servo_angle[servo_id - 1]


def getPWMServoPulse(servo_id):
    if servo_id < 1 or servo_id > 6:
        raise AttributeError("Invalid Servo ID: %d" % servo_id)
    return __servo_pulse[servo_id - 1]


def getBattery():
    result = _i2c_write_read([__ADC_BAT_ADDR], 2)
    if result[0] >= 2:
        return result[1] | (result[2] << 8)
    return 0


def setBuzzer(new_state):
    pi.set_mode(BUZZER_PIN, pigpio.OUTPUT)
    pi.write(BUZZER_PIN, new_state)


def setLed1(state):
    global _led1_state
    _led1_state = state.value if isinstance(state, LEDState) else (1 if state else 0)
    pi.write(LED1_PIN, _led1_state)


def getLed1():
    return _led1_state


def setLed2(state):
    global _led2_state
    _led2_state = state.value if isinstance(state, LEDState) else (1 if state else 0)
    pi.write(LED2_PIN, _led2_state)


def getLed2():
    return _led2_state


def getKey1():
    """Return True while key 1 is pressed (active low)."""
    return pi.read(KEY1_PIN) == 0


def getKey2():
    """Return True while key 2 is pressed (active low)."""
    return pi.read(KEY2_PIN) == 0


def test_drive(speed=60, duration=1.0):
    """
    Drive test: forward → stop → reverse → stop.
    Runs motors 1 (left) and 2 (right) at ±speed% for duration seconds each.
    Call after initialize(). Robot will move — clear the area first.
    """
    log = logging.getLogger(__name__)
    log.warning("Drive test starting in 2 s — robot will move!")
    time.sleep(2)

    log.info("Forward at %d%%", speed)
    setMotor(1, speed)
    setMotor(2, speed)
    time.sleep(duration)

    log.info("Stop")
    setMotor(1, 0)
    setMotor(2, 0)
    time.sleep(0.5)

    log.info("Reverse at %d%%", speed)
    setMotor(1, -speed)
    setMotor(2, -speed)
    time.sleep(duration)

    log.info("Stop")
    setMotor(1, 0)
    setMotor(2, 0)
    log.info("Drive test complete")


def test_servos(servo_ids=None, offset=10, move_time_ms=500):
    """
    Servo test: centre each servo, nudge ±offset degrees, return to centre.
    servo_ids: list of servo IDs to test (1–6); defaults to all six.
    Call after initialize().
    """
    log = logging.getLogger(__name__)
    if servo_ids is None:
        servo_ids = list(range(1, 7))
    center = 90
    pause = move_time_ms / 1000.0 + 0.1
    for sid in servo_ids:
        log.info("Servo %d: centre → -%d° → centre → +%d° → centre", sid, offset, offset)
        setPWMServoAngle(sid, center)
        time.sleep(pause)
        setPWMServoAngle(sid, center - offset)
        time.sleep(pause)
        setPWMServoAngle(sid, center)
        time.sleep(pause)
        setPWMServoAngle(sid, center + offset)
        time.sleep(pause)
        setPWMServoAngle(sid, center)
        time.sleep(pause)
    log.info("Servo test complete")


if __name__ == "__main__":
    import argparse
    import logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(description="GoGoPi board hardware test")
    parser.add_argument("--motors", action="store_true", help="run drive motor test (robot will move)")
    parser.add_argument(
        "--servos", nargs="*", type=int, metavar="ID",
        help="run servo nudge test; optionally list servo IDs 1-6 (default: all)",
    )
    args = parser.parse_args()

    initialize()
    try:
        setLed1(LEDState.ON)
        setLed2(LEDState.ON)
        setPixelColor(0, 255, 0, 0)
        setPixelColor(1, 0, 255, 0)
        show()
        setBuzzer(1)
        time.sleep(1)
        setBuzzer(0)
        setLed1(LEDState.OFF)
        setLed2(LEDState.OFF)
        setPixelColor(0, 0, 0, 255)
        setPixelColor(1, 255, 255, 255)
        show()
        if args.motors:
            test_drive()
        if args.servos is not None:
            test_servos(servo_ids=args.servos or None)
    finally:
        setMotor(1, 0)
        setMotor(2, 0)
        cleanup()
