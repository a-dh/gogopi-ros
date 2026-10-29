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

# YDLidar SDK installs its Python bindings here (cmake install, not pip)
_YDLIDAR_DIST = "/usr/local/lib/python3/dist-packages"
if _YDLIDAR_DIST not in sys.path:
    sys.path.append(_YDLIDAR_DIST)

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


def _lidar_node_running() -> bool:
    r = subprocess.run(["pgrep", "-f", "lidar_node"], capture_output=True)
    return r.returncode == 0


def _thermal_node_running() -> bool:
    r = subprocess.run(
        ["pgrep", "-f", "thermal_node"],
        capture_output=True,
    )
    return r.returncode == 0


def _camera_node_running() -> bool:
    r = subprocess.run(["pgrep", "-f", "camera_node"], capture_output=True)
    return r.returncode == 0


def _i2c_probe(bus: int, addr: int) -> tuple[bool, str]:
    try:
        from smbus2 import SMBus
        with SMBus(bus) as b:
            b.read_byte(addr)
        return True, ""
    except Exception as exc:
        return False, str(exc)


def _i2c_probe_mlx90640(bus: int) -> tuple[bool, str]:
    # MLX90640 requires a 16-bit register address before any read.  A bare
    # read_byte (no preceding register write) leaves the sensor in a corrupted
    # state that requires a power cycle to clear.  Read the status register
    # (0x8000) properly via i2c_rdwr instead.
    try:
        from smbus2 import SMBus, i2c_msg
        with SMBus(bus) as b:
            wr = i2c_msg.write(0x33, [0x80, 0x00])
            rd = i2c_msg.read(0x33, 2)
            b.i2c_rdwr(wr, rd)
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
        if addr == 0x33:
            ok, err = _i2c_probe_mlx90640(bus)
        else:
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
        note = ""
        if pigpio_ok:
            # a pin read proves GPIO access, not just the socket
            try:
                note = f"v{pi.get_pigpio_version()}, KEY1 (BCM 13) = {pi.read(13)}"
            except Exception as exc:
                pigpio_ok = False
                note = f"GPIO read failed: {exc}"
            pi.stop()
        _row(OK if pigpio_ok else BAD, "pigpiod  (Python client)", note)
        if not pigpio_ok:
            _fail("pigpiod Python client cannot connect", [
                "Check service        :  systemctl status pigpiod",
                "Start service        :  sudo systemctl start pigpiod",
                "Manual fallback      :  sudo pigpiod",
            ])
    except ImportError:
        _row(BAD, "pigpiod  (Python client)", "pigpio package not installed")
        _fail("pigpio Python package missing", [
            "Install              :  sudo make install  in ~/pigpio  (README → Install → pigpio)",
            "  or, SDK in a venv  :  pip install -e .  (pulls pigpio from pyproject.toml)",
        ])
    except Exception as exc:
        _row(BAD, "pigpiod  (Python client)", str(exc))
        _fail("pigpiod Python client error", [
            "Check service        :  systemctl status pigpiod",
        ])

    # rpi-led-proxy client
    led_ok = False
    try:
        from wsled_client import WSLEDClient
        WSLEDClient()
        led_ok = True
        _row(OK, "rpi-led-proxy  (WSLEDClient)")
    except ImportError:
        _row(BAD, "rpi-led-proxy  (WSLEDClient)", "wsled_client package not installed")
        _fail("wsled_client package missing", [
            "Install              :  pip install rpi-led-proxy",
        ])
    except Exception as exc:
        _row(BAD, "rpi-led-proxy  (WSLEDClient)", str(exc))
        _fail("rpi-led-proxy WSLEDClient failed to connect", [
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
            v = raw / 1000.0
            if v >= 7.8:
                icon, note = OK, f"{v:.2f} V  (good)"
            elif v >= 7.2:
                icon, note = SKIP, f"{v:.2f} V  (moderate — consider charging)"
            else:
                icon, note = BAD, f"{v:.2f} V  (low — charge now)"
                _fail("battery low", ["Charge the battery before extended use"])
            _row(icon, "battery", note)
        except Exception as exc:
            _row(BAD, "battery", str(exc))
            _fail("battery read failed", [
                "Verify I2C 0x7A      :  i2cdetect -y 1  (look for 7a)",
                "Verify pigpiod       :  systemctl status pigpiod",
            ])
    else:
        _row(SKIP, "battery", "pigpiod or I2C 0x7A unavailable")

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


def check_lidar() -> None:
    print(f"\n{_B}  LiDAR (YDLidar TX8){_N}")

    port = "/dev/ydlidar"

    # serial device present
    device_ok = os.path.exists(port)
    _row(OK if device_ok else BAD, f"serial device  {port}")
    if not device_ok:
        _fail(f"LiDAR serial device {port!r} not found", [
            "Check udev rule      :  ls -la /dev/ydlidar",
            "Check USB cable      :  lsusb | grep -i serial",
            "Plug in the LiDAR and retry",
        ])

    # dialout group (serial port access)
    try:
        import grp as _grp, pwd as _pwd
        user = os.environ.get("USER") or _pwd.getpwuid(os.getuid()).pw_name
        user_groups = [g.gr_name for g in _grp.getgrall() if user in g.gr_mem]
        in_dialout = "dialout" in user_groups
        _row(OK if in_dialout else BAD, "user in 'dialout' group")
        if not in_dialout:
            _fail("user not in 'dialout' group (serial access denied)", [
                f"Fix  :  sudo usermod -aG dialout {user}  (then log out / in)",
            ])
    except Exception as exc:
        _row(BAD, "dialout group check", str(exc))

    # ydlidar package (path injected at module top)
    _ydlidar = None
    ydlidar_ok = False
    try:
        import ydlidar as _ydlidar
        ydlidar_ok = True
        _row(OK, "ydlidar Python package")
    except ImportError as exc:
        _row(BAD, "ydlidar Python package", str(exc))
        _fail("ydlidar package not installed", [
            "Build and install YDLidar-SDK  :  see CLAUDE.md prerequisites",
        ])

    # SDK probe — initialize only (no turnOn / no motor spin)
    if device_ok and ydlidar_ok:
        if _lidar_node_running():
            _row(OK, "YDLidar SDK probe", "skipped — lidar_node active")
        else:
            try:
                _ydlidar.os_init()
                laser = _ydlidar.CYdLidar()
                laser.setlidaropt(_ydlidar.LidarPropSerialPort,          port)
                laser.setlidaropt(_ydlidar.LidarPropSerialBaudrate,      115200)
                laser.setlidaropt(_ydlidar.LidarPropLidarType,           _ydlidar.TYPE_TOF)
                laser.setlidaropt(_ydlidar.LidarPropDeviceType,          _ydlidar.YDLIDAR_TYPE_SERIAL)
                laser.setlidaropt(_ydlidar.LidarPropSingleChannel,       True)
                laser.setlidaropt(_ydlidar.LidarPropSupportMotorDtrCtrl, True)
                ok = laser.initialize()
                laser.disconnecting()
                if ok:
                    _row(OK, "YDLidar SDK probe  (initialize)")
                else:
                    _row(BAD, "YDLidar SDK probe  (initialize failed)")
                    _fail("YDLidar initialize() failed", [
                        f"Check device   :  ls -la {port}",
                        "Check power    :  LiDAR needs 5 V supply",
                        "Check baud     :  TX series uses 115200",
                    ])
            except Exception as exc:
                _row(BAD, "YDLidar SDK probe", str(exc))
                _fail("YDLidar SDK probe failed", [
                    f"Check device   :  ls -la {port}",
                    "Check power    :  LiDAR needs 5 V supply",
                ])
    elif not device_ok:
        _row(SKIP, "YDLidar SDK probe", "serial device absent")
    else:
        _row(SKIP, "YDLidar SDK probe", "ydlidar package missing")


def check_camera() -> None:
    print(f"\n{_B}  Camera (HBVCAM USB){_N}")

    dev = "/dev/v4l/by-id/usb-HBVCAM_CAMERA_HBVCAM_CAMERA-video-index0"

    # by-id link present (survives USB re-enumeration as a different /dev/videoN)
    device_ok = os.path.exists(dev)
    _row(OK if device_ok else BAD, "device  (by-id)", os.path.realpath(dev) if device_ok else dev)
    if not device_ok:
        _fail("USB camera not found", [
            "Check USB            :  lsusb | grep -i hbv",
            "List video devices   :  ls -la /dev/v4l/by-id/",
            "Check cable          :  the USB data wire has snapped at its connector before",
        ])

    # video group (device access)
    try:
        import grp as _grp, pwd as _pwd
        user = os.environ.get("USER") or _pwd.getpwuid(os.getuid()).pw_name
        user_groups = [g.gr_name for g in _grp.getgrall() if user in g.gr_mem]
        in_video = "video" in user_groups
        _row(OK if in_video else BAD, "user in 'video' group")
        if not in_video:
            _fail("user not in 'video' group (camera access denied)", [
                f"Fix  :  sudo usermod -aG video {user}  (then log out / in)",
            ])
    except Exception as exc:
        _row(BAD, "video group check", str(exc))

    # frame grab — skipped while camera_node holds the device
    if not device_ok:
        _row(SKIP, "frame grab", "device absent")
    elif _camera_node_running():
        _row(OK, "frame grab", "skipped — camera_node active")
    else:
        try:
            import cv2
            cap = cv2.VideoCapture(dev, cv2.CAP_V4L2)
            try:
                frame = None
                if cap.isOpened():
                    # the first reads after open can come back empty
                    for _ in range(5):
                        ok, frame = cap.read()
                        if ok:
                            break
                        frame = None
            finally:
                cap.release()
            if frame is not None:
                h, w = frame.shape[:2]
                _row(OK, "frame grab", f"{w}x{h}")
            else:
                _row(BAD, "frame grab", "no frame")
                _fail("camera opened but returned no frame", [
                    "Check for other users  :  fuser -v /dev/video*",
                    "Replug the camera and retry",
                ])
        except ImportError as exc:
            _row(BAD, "frame grab  (opencv)", str(exc))
            _fail("OpenCV missing", [
                "Install  :  sudo apt install python3-opencv",
            ])
        except Exception as exc:
            _row(BAD, "frame grab", str(exc))
            _fail("camera frame grab failed", [
                f"Check device   :  ls -la {dev}",
            ])


def check_ros2() -> None:
    print(f"\n{_B}  ROS2{_N}")
    ws = os.path.join(_REPO, "ros2")

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

    check_lidar()
    check_camera()
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
