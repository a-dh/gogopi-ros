# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository layout

```
gopiros2/
├── HiwonderSDK/           ← Hardware SDK (pip-installed into system Python for ROS2)
│   ├── Board.py           ← Motors, servos, buzzer, GPIO LEDs, NeoPixels, buttons
│   ├── Sonar.py           ← Ultrasonic sensor + 2 NeoPixels on sensor module
│   ├── Misc.py            ← map(), setRange(), emptyFunc()
│   └── PID.py             ← Generic PID controller
├── services/              ← Standalone systemd services (run independently of ROS2)
│   ├── hw_button_scan.py  ← KEY1 long-press=WiFi reset, KEY2 long-press=shutdown
│   ├── hw_wifi.py         ← WiFi AP/client manager with LED status blink
│   └── hw_find.py         ← UDP discovery beacon
├── ros2/                  ← Colcon workspace
│   └── src/
│       ├── gogopi_msgs/   ← Custom msg/srv (CMake)
│       └── gogopi_bringup/← Lifecycle nodes + launch files (ament_python)
├── tools/
│   ├── pt_range_check.py  ← pan/tilt end stops, level, scales (camera-based; run on robot)
│   └── pt_lidar_stress.py ← swing pan hard, measure lidar glitches / battery / camera fps
├── pyproject.toml         ← gogopi-sdk pip package (HiwonderSDK) + gogopi-install entry point
└── gogopi/install.py      ← console script: sudo gogopi-install install|remove
```

## Commands

```bash
# Set up Python environment (run from repo root)
python3 -m venv venv && . venv/bin/activate
pip install -e .                       # install gogopi-sdk (HiwonderSDK)

# Build and install systemd services as a Debian package
# (requires dpkg-deb and SSH access to gratzi for rpi-led-proxy — use ssh -A)
# Run ON THE ROBOT: the .deb bundles a venv tied to the host arch + Python version;
# build_deb.sh refuses to run on anything but aarch64.
./packaging/build_deb.sh
sudo dpkg -i packaging/gogopi-services_*.deb

# Remove the package
sudo dpkg -r gogopi-services

# Install gogopi-sdk into system Python (required for ROS2 nodes — they use /usr/bin/python3, not the venv)
pip3 install --user --break-system-packages -e .

# ROS2 apt packages (beyond ros-jazzy-ros-base, install once)
sudo apt install \
    ros-jazzy-ros2lifecycle \              # ros2 lifecycle CLI (set/get node states)
    ros-jazzy-ros2run \                   # ros2 run CLI (launch individual nodes)
    ros-jazzy-compressed-image-transport  # JPEG compression for camera topics (required for RViz2 over VPN)

# YDLidar SDK — build and install once (system-wide prerequisite for lidar_node)
# Source: https://github.com/YDLIDAR/YDLidar-SDK  (currently at tag V1.2.7)
# Installs libydlidar_sdk.a + Python bindings to /usr/local/lib/python3/dist-packages/
git clone https://github.com/YDLIDAR/YDLidar-SDK.git ~/YDLidar-SDK
cd ~/YDLidar-SDK && mkdir build && cd build
cmake .. && make -j4
sudo make install
cd -

# Required system daemons (must be running before any SDK or ROS2 node use)
sudo systemctl start pigpiod
sudo systemctl start rpi-led-proxy     # WS281x LED daemon

# ROS2 build (from ros2/ directory, after sourcing ROS2)
source /opt/ros/jazzy/setup.bash
cd ros2
colcon build
source install/setup.bash

# Run full robot
ros2 launch gogopi_bringup robot.launch.py

# Run sensors only (safe — no motors)
ros2 launch gogopi_bringup sensors_only.launch.py

# Manually transition lifecycle nodes after launch
ros2 lifecycle set /board_node configure
ros2 lifecycle set /board_node activate

# Git push behavior
This repository has a local `.git/hooks/pre-push` hook that mirrors the same pushed refs to all configured remotes. Push commands from this repo will automatically attempt to update every remote.
```

## SDK architecture

### Board.py — two-phase design

`Board.py` has **no module-level side-effects**. GPIO is only touched after `initialize()` is called, and released by `cleanup()`. This is required for lifecycle nodes.

```python
import HiwonderSDK.Board as Board
Board.initialize()   # connect pigpiod, configure pins, blank NeoPixels
# ... use Board functions ...
Board.cleanup()      # release pigpiod connection
```

**Hardware buses:**
- I2C bus 1, address `0x7A`: expansion board (motors, servos, battery ADC)
- I2C bus 1, address `0x77`: Sonar module (Sonar.py, smbus2)
- GPIO via pigpiod: buzzer (BCM 6), LED1 (BCM 16, hw_wifi only — read-only in the SDK), LED2 (BCM 26), KEY1 (BCM 13), KEY2 (BCM 23)
- WS281x via `rpi-led-proxy` Unix socket (`LEDClient` from the `rpi-led-proxy` package)

**NeoPixels:** `setPixelColor(index, r, g, b)` buffers; `show()` sends all pixels to the `led-proxy` daemon at 47% brightness (`__RGB_BRIGHTNESS = 120/255`).

### Sonar.py — stateless per-call

Uses `smbus2` with `with SMBus(...) as bus:` per call. No `initialize()`/`cleanup()` needed. `getDistance()` returns mm (capped at 5000). The Sonar module also carries 2 NeoPixels controlled via its own I2C registers (not the led-proxy).

## ROS2 node architecture

All nodes use **ROS2 lifecycle** (`LifecycleNode`). Nodes start in unconfigured state; an operator or a launch manager must drive transitions.

| Node | Executable | Key dependency |
|---|---|---|
| `board_node` | `board_node` | pigpiod, I2C 0x7A, rpi-led-proxy |
| `sonar_node` | `sonar_node` | I2C 0x77 |
| `camera_node` | `camera_node` | USB camera via `/dev/v4l/by-id/usb-HBVCAM_CAMERA_HBVCAM_CAMERA-video-index0` (OpenCV V4L2) |
| `thermal_node` | `thermal_node` | I2C 0x33 (MLX90640) |
| `lidar_node` | `lidar_node` | `/dev/ydlidar` (YDLidar TX8, USB-serial) |
| `robot_state_publisher` | `robot_state_publisher` | URDF (`gogopi.urdf`), `/gogopi/servo_state` → TF tree |

### board_node — `gogopi_bringup/board_node.py`

`on_configure` calls `Board.initialize()`. `on_activate` homes both servos to joint 0 (tilt 28° = level, pan 90°; `home_on_activate`, default true — the SDK can't read servo positions back) and starts publish timers. `on_deactivate` stops motors and cancels timers. `on_cleanup`/`on_shutdown` calls `Board.cleanup()`.

| Topic / Service | Type | Direction |
|---|---|---|
| `/gogopi/cmd_vel` | `geometry_msgs/Twist` | SUB → differential drive |
| `/gogopi/motors_raw` | `std_msgs/Int16MultiArray` | SUB → direct per-motor |
| `/gogopi/servo_cmd` | `gogopi_msgs/ServoCommand` | SUB |
| `/joint_states` | `sensor_msgs/JointState` | SUB → `tilt_joint`/`pan_joint` servos (from `joint_state_publisher_gui` in the quattro RViz container) |
| `/gogopi/board_pixels` | `gogopi_msgs/PixelArray` | SUB |
| `/gogopi/buzzer` | `std_msgs/Bool` | SUB |
| `/gogopi/led2` | `std_msgs/Bool` | SUB |
| `/gogopi/battery_raw` | `std_msgs/UInt16` | PUB 2 Hz |
| `/gogopi/battery_voltage` | `std_msgs/Float32` | PUB 2 Hz (volts, confirmed 1 mV/count) |
| `/gogopi/buttons` | `sensor_msgs/Joy` | PUB 50 Hz |
| `/gogopi/servo_state` | `sensor_msgs/JointState` | PUB 10 Hz |
| `/gogopi/motor_state` | `std_msgs/Float32MultiArray` | PUB 10 Hz |
| `/gogopi/set_servo` | `gogopi_msgs/srv/SetServo` | SRV |
| `/gogopi/set_buzzer` | `std_srvs/SetBool` | SRV |
| `/gogopi/e_stop` | `std_srvs/Trigger` | SRV |

Servo travel limits: `servo_min_deg` [17, 40] / `servo_max_deg` [90, 140] (pan temporarily 40–140° until the tower wiring is rerouted — wide pan swings snapped the camera's USB data wire at its connector) (servo 1 tilt, servo 2 pan) clamp every servo command (angle, pulse, `/joint_states`). Tilt (measured with `tools/pt_range_check.py`): hard stop ~6°; a cable touches from ~14° (that's where the servo first sounded stalled). 17° keeps clear of the cable; 9° possible once the cable is rerouted. Tilt moves freely to the servo's 180°, but above 90° (camera ~67° up) panning binds the cables, so 90° is the practical max.

Servo ↔ joint mapping: `joint_zero_servo_deg` [28, 90] (servo angle where the joint is 0) and `joint_deg_per_servo_deg` [1.08, 1.0]. Tilt: `tilt_joint`° = 1.08 · (28 − servo°), +down, 0 = **level** (level from `pt_range_check.py pan-check`: image twist while panning vanishes at servo 28°; scale from an inclinometer: servo 90° ≈ 67° up; the SG9 repeatability is low). Pan: `pan_joint`° = servo° − 90 (1.0 measured from image shift vs. focal length from a shelf of known width). URDF `tilt_joint` limits −1.1687 … 0.2073 rad mirror the 90°/17° clamps; keep URDF and these parameters in sync.

`cmd_vel` kinematic conversion: `left = v − ω·d/2`, `right = v + ω·d/2`, scaled by `max_speed_pct`. Parameters: `wheel_separation_m` (default 0.15), `max_speed_pct` (default 80).

`/joint_states` maps URDF joint positions (radians) to servo angles with the inverse of that mapping (tilt servo = 28 − deg(pos)/1.08, pan servo = 90 + deg(pos)), the inverse of what `/gogopi/servo_state` publishes. Changes under 1° per servo are ignored, so a steady stream of messages doesn't keep re-sending the same command. It's ignored while the node is inactive.

`battery_raw` publishes raw ADC counts; `battery_voltage` publishes the same value divided by 1000 (confirmed 1 mV/count).

### sonar_node — `gogopi_bringup/sonar_node.py`

`on_configure` creates `Sonar()` and sets RGB mode 0. `on_activate` starts 10 Hz range timer.

| Topic | Type | Direction |
|---|---|---|
| `/gogopi/sonar/range` | `sensor_msgs/Range` | PUB 10 Hz |
| `/gogopi/sonar/pixels` | `gogopi_msgs/PixelArray` | SUB |
| `/gogopi/sonar/rgb_mode` | `std_msgs/UInt8` | SUB |

### camera_node — `gogopi_bringup/camera_node.py`

USB camera via OpenCV (`python3-opencv`). `on_configure` opens the device with `cv2.CAP_V4L2` (forced, so GStreamer doesn't intercept the device path) and sets the resolution. `on_activate` starts the capture timer. `on_cleanup`/`on_shutdown` release the device. If no frame has arrived for 2 s it releases and reopens the device (at most every 2 s) — the camera can drop off USB and come back as a different `/dev/videoN`.

| Topic | Type | Direction |
|---|---|---|
| `/gogopi/camera/image_raw` | `sensor_msgs/Image` (`bgr8`) | PUB `image_hz` |

The camera is mounted rolled 90° (world up = raw image left); `gprctl` and `robot.launch.py` set `image_rotation` 90 so the published image is upright.

Parameters: `camera_device` (default `/dev/video0`; `gprctl` and `robot.launch.py` pass the by-id path above, which survives USB re-enumeration), `frame_id` (`camera_link`), `image_width` (640), `image_height` (480), `image_hz` (15.0), `image_rotation` (0/90/180/270).

Published with a plain rclpy publisher, so no `/compressed` topic exists on the robot by default. Image-transport plugins only hook into C++ `image_transport` publishers.

### lidar_node — `gogopi_bringup/lidar_node.py`

YDLidar TX8 (TOF LIDAR, single-channel, DTR motor control, 4–8 Hz scan, 0.1–8 m range). `on_configure` opens the serial device and calls `initialize()`. `on_activate` calls `turnOn()` and starts a background scan thread + drain timer. `on_deactivate` calls `turnOff()` and joins the thread.

Serial device: **`/dev/ydlidar`** (udev rule — do not use `/dev/ttyUSBx` directly).

| Topic | Type | Direction |
|---|---|---|
| `/gogopi/lidar/scan` | `sensor_msgs/LaserScan` | PUB ~7 Hz |

Key parameters (all have defaults): `port` (`/dev/ydlidar`), `baudrate` (115200), `frame_id` (`lidar_link`), `scan_hz` (7.0, valid 4–8), `range_min` (0.1 m), `range_max` (8.0 m), `single_channel` (true), `motor_dtr_ctrl` (true).

Published scan is a 360-bin uniform grid (1°/bin) over −π … +π. Bins with no return are `inf`.

## Services and WiFi/power semantics

**hw_button_scan** (systemd service, uses pigpiod):
- KEY1 (BCM 13) held 2 s → `systemctl restart hw_wifi.service` (WiFi factory reset)
- KEY2 (BCM 23) held 2 s → `systemctl poweroff`
- Battery check every 10 min: 30 ADC samples over ~3 s (0x7A), median. Spread > 0.3 V or median < 5.5 V → no battery (USB only — the ADC floats ~4.5–8.3 V, and can look steady for ~1 s, so 3 s of samples). < 7.2 V → `wall` warning; < 6.8 V on three steady checks 30 s apart → poweroff
- Buzzer (BCM 6): while USB-powered, checks every 60 s and beeps 0.25 s each time. Every shutdown (KEY2 or battery) buzzes 5 s, turns the buzzer off, then powers off. The buzzer works with the main board switch off. `touch /tmp/gogopi-mute` silences the USB beep only (cleared on reboot; `rm` to unmute)
- Must start after `pigpiod.service` (declared in service file)

**hw_wifi** (systemd service, uses pigpiod):
- Controls LED1 (BCM 16, active-low) via pigpiod to indicate WiFi state:
  - AP mode starting: fast blink (50 ms on / 50 ms off)
  - Client mode connecting: very fast blink (5 ms / 5 ms)
  - Client connected: solid on
- WiFi config in `/etc/Hiwonder/hiwonder_wifi_conf.py` or `/boot/hiwonder_wifi_conf.py`
- **LED1 ownership**: LED1 is reserved for `hw_wifi`. The SDK has no `setLed1()` and never configures the pin; `Board.getLed1()` only reads the level. ROS nodes must not write it.

## gogopi_msgs custom types

- **`PixelArray.msg`**: `uint8[] index` + `std_msgs/ColorRGBA[] color` (float 0-1 per channel). Equal-length arrays. Used for both board and sonar NeoPixels.
- **`ServoCommand.msg`**: `servo_id`, `use_pulse` flag, `pulse_us` (500-2500), `angle_deg` (0-180), `move_time_ms`.
- **`SetServo.srv`**: wraps `ServoCommand`, returns `bool success` + `string message`.
