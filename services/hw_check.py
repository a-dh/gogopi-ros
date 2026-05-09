#!/usr/bin/env python3
"""Hardware health check. Reports status without starting any services."""

import os
import sys
import subprocess
import warnings
warnings.filterwarnings("ignore", category=RuntimeWarning, module="adafruit_blinka")

# make HiwonderSDK importable from any cwd
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

# ── ANSI ──────────────────────────────────────────────────────────────────────
_R = "\033[31m"
_G = "\033[32m"
_Y = "\033[33m"
_C = "\033[36m"
_B = "\033[1m"
_N = "\033[0m"
OK   = f"{_G}✓{_N}"
BAD  = f"{_R}✗{_N}"
SKIP = f"{_Y}~{_N}"

_issues: list[tuple[str, list[str]]] = []


def _row(icon: str, label: str, note: str = "") -> None:
    suffix = f"   {_C}{note}{_N}" if note else ""
    print(f"  {icon}  {label}{suffix}")


def _fail(title: str, hints: list[str]) -> None:
    _issues.append((title, hints))


# ── helpers ───────────────────────────────────────────────────────────────────

def _svc_state(name: str) -> str:
    r = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True)
    return r.stdout.strip()  # active | inactive | failed | not-found | …


def _svc_result(name: str) -> str:
    r = subprocess.run(
        ["systemctl", "show", name, "--property=Result"],
        capture_output=True, text=True,
    )
    for line in r.stdout.splitlines():
        if line.startswith("Result="):
            return line.split("=", 1)[1].strip()
    return ""


def _thermal_node_running() -> bool:
    r = subprocess.run(
        ["pgrep", "-f", "thermal_node"],
        capture_output=True,
    )
    return r.returncode == 0


def _i2c_probe(bus: int, addr: int) -> tuple[bool, str]:
    try:
        from smbus2 import SMBus
        with SMBus(bus) as b:
            b.read_byte(addr)
        return True, ""
    except Exception as exc:
        return False, str(exc)


# ── check blocks ──────────────────────────────────────────────────────────────

def check_services() -> dict[str, str]:
    print(f"\n{_B}  Services{_N}")
    items = [
        ("pigpiod",        "GPIO daemon"),
        ("rpi-led-proxy",  "WS281x LED daemon"),
        ("hw_wifi",        "WiFi manager"),
        ("hw_button_scan", "button scanner"),
    ]
    out: dict[str, str] = {}
    for svc, desc in items:
        st = _svc_state(svc)
        out[svc] = st

        # hw_wifi exits cleanly after connecting; treat that as healthy
        if svc == "hw_wifi" and st == "inactive":
            result = _svc_result(svc)
            if result == "success":
                _row(OK, svc, f"exited-ok (connected)  — {desc}")
            else:
                _row(SKIP, svc, f"inactive (not yet started)  — {desc}")
            continue

        if st == "active":
            icon = OK
        elif st == "not-found":
            icon = SKIP
        else:
            icon = BAD
            _fail(f"service {svc!r} is {st!r}", [
                f"View logs    :  journalctl -u {svc} -n 30 --no-pager",
                f"Start        :  sudo systemctl start {svc}",
                f"Enable boot  :  sudo systemctl enable {svc}",
            ])
        _row(icon, svc, f"{st}  — {desc}")
    return out


def check_i2c() -> dict[tuple[int, int], bool]:
    print(f"\n{_B}  I2C{_N}")
    devices = [
        (1, 0x7A, "expansion board (motors / servos / ADC)"),
        (1, 0x77, "sonar module"),
        (1, 0x33, "MLX90640 thermal camera"),
    ]
    out: dict[tuple[int, int], bool] = {}
    for bus, addr, desc in devices:
        ok, err = _i2c_probe(bus, addr)
        out[(bus, addr)] = ok
        _row(OK if ok else BAD, f"bus {bus} / 0x{addr:02X}  {desc}", "" if ok else err)
        if not ok:
            _fail(f"I2C 0x{addr:02X} ({desc.split()[0]}) not responding", [
                "Scan bus             :  i2cdetect -y 1",
                "Check I2C enabled    :  raspi-config → Interface Options → I2C",
                "Check cable / power  :  reseat connector, inspect for bent pins",
                "Group membership     :  groups | grep i2c   →  sudo usermod -aG i2c $USER",
            ])
    return out


def check_daemons() -> tuple[bool, bool]:
    print(f"\n{_B}  Daemon connectivity{_N}")

    # pigpiod Python client
    pigpio_ok = False
    try:
        import pigpio
        pi = pigpio.pi()
        pigpio_ok = pi.connected
        if pigpio_ok:
            pi.stop()
        _row(OK if pigpio_ok else BAD, "pigpiod  (Python client)")
        if not pigpio_ok:
            _fail("pigpiod Python client cannot connect", [
                "Check service        :  systemctl status pigpiod",
                "Start service        :  sudo systemctl start pigpiod",
                "Manual fallback      :  sudo pigpiod",
            ])
    except ImportError:
        _row(BAD, "pigpiod  (Python client)", "pigpio package not installed")
        _fail("pigpio Python package missing", [
            "Install              :  pip install pigpio",
        ])
    except Exception as exc:
        _row(BAD, "pigpiod  (Python client)", str(exc))
        _fail("pigpiod Python client error", [
            "Check service        :  systemctl status pigpiod",
        ])

    # rpi-led-proxy client
    led_ok = False
    try:
        from led_client import LEDClient
        LEDClient()
        led_ok = True
        _row(OK, "rpi-led-proxy  (LEDClient)")
    except ImportError:
        _row(BAD, "rpi-led-proxy  (LEDClient)", "led_client package not installed")
        _fail("led_client package missing", [
            "Install              :  pip install rpi-led-proxy",
        ])
    except Exception as exc:
        _row(BAD, "rpi-led-proxy  (LEDClient)", str(exc))
        _fail("rpi-led-proxy LEDClient failed to connect", [
            "Check service logs   :  journalctl -u rpi-led-proxy -n 20 --no-pager",
            "Start service        :  sudo systemctl start rpi-led-proxy",
        ])

    return pigpio_ok, led_ok


def check_hardware_reads(pigpio_ok: bool, expansion_ok: bool, sonar_ok: bool, thermal_ok: bool) -> None:
    print(f"\n{_B}  Hardware reads{_N}")

    if pigpio_ok and expansion_ok:
        try:
            import HiwonderSDK.Board as Board
            Board.initialize()
            raw = Board.getBattery()
            Board.cleanup()
            _row(OK, "battery ADC  (raw counts)", str(raw))
        except Exception as exc:
            _row(BAD, "battery ADC  (raw counts)", str(exc))
            _fail("battery ADC read failed", [
                "Verify I2C 0x7A      :  i2cdetect -y 1  (look for 7a)",
                "Verify pigpiod       :  systemctl status pigpiod",
            ])
    else:
        _row(SKIP, "battery ADC  (raw counts)", "pigpiod or I2C 0x7A unavailable")

    if sonar_ok:
        try:
            from HiwonderSDK.Sonar import Sonar
            dist = Sonar().getDistance()
            _row(OK, "sonar range  (mm)", str(dist))
        except Exception as exc:
            _row(BAD, "sonar range  (mm)", str(exc))
            _fail("sonar read failed", [
                "Verify I2C 0x77      :  i2cdetect -y 1  (look for 77)",
                "Power cycle the sonar module",
            ])
    else:
        _row(SKIP, "sonar range  (mm)", "I2C 0x77 unavailable")

    if thermal_ok:
        if _thermal_node_running():
            _row(OK, "MLX90640 thermal  (I2C present)", "frame read skipped — thermal_node active")
        else:
            try:
                import board
                import busio
                import adafruit_mlx90640
                i2c = busio.I2C(board.SCL, board.SDA, frequency=400000)
                mlx = adafruit_mlx90640.MLX90640(i2c)
                frame = [0.0] * 768
                mlx.getFrame(frame)
                i2c.deinit()
                _row(OK, "MLX90640 thermal  (°C centre pixel)", f"{frame[384]:.1f}")
            except ImportError as exc:
                _row(BAD, "MLX90640 thermal  (library)", str(exc))
                _fail("adafruit-mlx90640 library missing", [
                    "Install  :  pip install adafruit-circuitpython-mlx90640",
                ])
            except Exception as exc:
                _row(BAD, "MLX90640 thermal  (read)", str(exc))
                _fail("MLX90640 read failed", [
                    "Verify I2C 0x33      :  i2cdetect -y 1  (look for 33)",
                    "Check power / cable  :  reseat connector",
                ])
    else:
        _row(SKIP, "MLX90640 thermal  (read)", "I2C 0x33 unavailable")


def check_ros2() -> None:
    print(f"\n{_B}  ROS2{_N}")
    ws = "/home/albertd/gopiros2/ros2"

    built = os.path.isdir(os.path.join(ws, "install"))
    _row(OK if built else BAD, "workspace built  (ros2/install/)")
    if not built:
        _fail("ROS2 workspace not built", [
            f"Build  :  cd {ws} && source /opt/ros/jazzy/setup.bash && colcon build",
        ])

    r = subprocess.run(["which", "ros2"], capture_output=True, text=True)
    sourced = r.returncode == 0
    _row(OK if sourced else BAD, "ros2 CLI on PATH")
    if not sourced:
        _fail("ros2 not on PATH", [
            "Source   :  source /opt/ros/jazzy/setup.bash",
            "Persist  :  echo 'source /opt/ros/jazzy/setup.bash' >> ~/.bashrc",
        ])


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    w = 56
    print(f"\n{_B}{'═' * w}")
    print(f"  GoPiRos2 Hardware Health Check")
    print(f"{'═' * w}{_N}")

    check_services()
    i2c = check_i2c()
    pigpio_ok, _led_ok = check_daemons()

    expansion_ok = i2c.get((1, 0x7A), False)
    sonar_ok     = i2c.get((1, 0x77), False)
    thermal_ok   = i2c.get((1, 0x33), False)
    check_hardware_reads(pigpio_ok, expansion_ok, sonar_ok, thermal_ok)

    check_ros2()

    # ── summary ───────────────────────────────────────────────────────────────
    n = len(_issues)
    print(f"\n{_B}{'═' * w}")
    if n == 0:
        print(f"  {_G}All checks passed ✓{_N}")
    else:
        print(f"  {_R}{n} issue{'s' if n != 1 else ''} found{_N}")
    print(f"{'═' * w}{_N}")

    # ── troubleshooting hints ─────────────────────────────────────────────────
    if _issues:
        print(f"\n{_B}  Troubleshooting{_N}\n")
        for title, hints in _issues:
            print(f"  {BAD}  {_B}{title}{_N}")
            for h in hints:
                print(f"       {h}")
            print()


if __name__ == "__main__":
    main()
