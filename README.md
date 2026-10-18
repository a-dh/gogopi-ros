# GoGoPi ROS2

ROS2 Jazzy driver stack for the GoGoPi differential-drive robot (Hiwonder expansion board on Raspberry Pi).

## Hardware wiring

| Signal | BCM | BOARD pin |
|---|---|---|
| I2C SDA / SCL | 2 / 3 | 3 / 5 |
| NeoPixels (rpi-led-proxy) | 12 | 32 |
| Buzzer | 6 | 31 |
| LED 1 | 16 | 36 |
| LED 2 | 26 | 37 |
| KEY 1 | 13 | 33 |
| KEY 2 | 23 | 16 |

## I2C bus layout

All I2C devices share **bus 1** (BCM 2/3):

| Device | Address | Driver |
|---|---|---|
| Expansion board (motors, servos, battery) | 0x7A | pigpio (via pigpiod) |
| Sonar module | 0x77 | smbus2 |
| MLX90640 thermal camera | 0x33 | adafruit-blinka (busio) |

There is no explicit application-level locking between the three drivers. All three reach the kernel through `/dev/i2c-1`, and the Linux I2C core holds a bus-adapter mutex for each transfer, so transactions cannot interleave and bus corruption is not a risk.

The practical consequence is **latency**: an MLX90640 frame read at 400 kHz takes ~4 ms, during which servo or sonar commands queue behind it. At typical publish rates (10 Hz sonar, 4 Hz thermal) the delay is negligible.

### Adding a ST VL53L5CX ToF sensor

Feasible. Address **0x29** has no conflict with existing devices. 400 kHz is supported and matches the current bus speed.

Two things to plan for:

- **Firmware load on every power-on.** The VL53L5CX has no flash; the host must push ~86 KB over I2C after each power cycle (~1.4 s at 400 kHz, bus blocked). Load firmware in `on_configure` before activating other nodes.
- **Large per-frame transfers.** An 8×8 result set is ~2000 bytes (~45 ms at 400 kHz, ~3× the MLX90640). At 5 Hz that consumes ~225 ms/s of bus time; servo and motor commands queue behind it, which is fine at human-scale reaction times.

**Pull-ups:** breakout boards (SparkFun Qwiic, Pimoroni) ship with 2.2 kΩ pull-ups. Cut the pull-up jumper on the breakout to avoid parallel pull-ups conflicting with those on the Hiwonder expansion board.

**Recommended driver:** [pimoroni/vl53l5cx-python](https://github.com/pimoroni/vl53l5cx-python) — ctypes wrapper around ST's ULD, uses `/dev/i2c-1` directly, coexists with smbus2 and adafruit-blinka.

## Camera Pan/Tilt Mechanism

The visual (USB) and thermal (MLX90640) cameras are mounted on a shared pan-tilt mechanism controlled by the robot's servos:

- **Servo 1** (ID 1): Controls **tilt** (up/down movement)
- **Servo 2** (ID 2): Controls **pan** (left/right movement)

### Adjusting Camera Orientation in ROS

To adjust the camera orientations, use the ROS service to set servo positions. Servo angles range from 0° to 180°.

**Set pan (servo 2) to 90° (center):**
```bash
ros2 service call /gogopi/set_servo gogopi_msgs/srv/SetServo "{command: {servo_id: 2, use_pulse: false, angle_deg: 90.0}}"
```

**Set tilt (servo 1) to 45°:**
```bash
ros2 service call /gogopi/set_servo gogopi_msgs/srv/SetServo "{command: {servo_id: 1, use_pulse: false, angle_deg: 45.0}}"
```

**Monitor servo states:**
```bash
ros2 topic echo /gogopi/servo_state
```

For persistent orientation adjustments, modify the launch parameters or use ROS parameter updates. The servo states are published as `sensor_msgs/JointState` with names `["servo_1", "servo_2"]` for integration with ROS TF and robot state publishers.

### Ensuring Consistent Image Orientations

To ensure the thermal and visual camera images have consistent "up" edges (e.g., both oriented with the top of the image corresponding to the upward direction), adjust the `image_rotation` parameter for each camera node. This rotates the image by 0°, 90°, 180°, or 270°.

**Set visual camera rotation to 180° (flip upside down):**
```bash
ros2 param set /camera_node image_rotation 180
```

**Set thermal camera rotation to 90° (rotate clockwise):**
```bash
ros2 param set /thermal_node image_rotation 90
```

### Calibrating Servo Angles

To calibrate servo angles against real-world positions, add angle offsets using the `servo_angle_offset_deg` parameter (array for servo 1 and 2). This adjusts the commanded angle before sending to the hardware.

**Set offsets for servo calibration:**
```bash
ros2 param set /board_node servo_angle_offset_deg "[5.0, -2.0]"  # +5° for servo 1, -2° for servo 2
```

Measure the actual servo positions and adjust offsets accordingly. For precise calibration, use ROS tools like `joint_state_publisher_gui` or implement a calibration routine.

### Power Saving and E-Stop for Servos

For power saving or emergency stop, servos can be disabled by stopping PWM output. The e-stop service now disables servos in addition to stopping motors.

**Trigger e-stop (stops motors and disables servos):**
```bash
ros2 service call /gogopi/e_stop std_srvs/Trigger
```

For manual servo disable, set pulse to 0 (may not be supported by all servo boards):
```bash
ros2 service call /gogopi/set_servo gogopi_msgs/srv/SetServo "{command: {servo_id: 1, use_pulse: true, pulse_us: 0}}"
```

### Power Management Modes

ROS2 idiom for power management uses services to switch between operational and low-power modes.

**Enter lowest power settings (stop publishing, disable motors/servos, turn off outputs):**
```bash
ros2 service call /gogopi/enter_power_save std_srvs/Trigger
```

**Resume normal power settings (restart publishing, ready to sense and move):**
```bash
ros2 service call /gogopi/exit_power_save std_srvs/Trigger
```

Use these services for battery conservation or standby modes. Lifecycle transitions can also be used for node-level power management.

**System packages**

```bash
sudo apt install pigpio i2c-tools python3-opencv
sudo raspi-config   # Interface Options → I2C → enable
```

**Daemons** — must be running before any SDK or ROS2 node use:

```bash
sudo systemctl enable --now pigpiod
sudo systemctl enable --now rpi-led-proxy   # from rpi-led-proxy .deb
```

`rpi-led-proxy` drives the WS281x NeoPixels via GPIO 12 (PWM0). See its own README for `.deb` build and install instructions.

**Python dependencies** — see [pyproject.toml](pyproject.toml).

## Hardware self-test

Run without ROS2, directly against the system Python that has the SDK installed:

```bash
/usr/bin/python3 HiwonderSDK/Board.py              # LEDs, NeoPixels, buzzer
/usr/bin/python3 HiwonderSDK/Board.py --servos     # pan/tilt nudge ±10°
/usr/bin/python3 HiwonderSDK/Board.py --motors     # forward/stop/reverse (robot moves)
```

Expected results:

| Test | Expected |
|---|---|
| Default | Both LEDs flash on; NeoPixels cycle red→green then blue→white; buzzer beeps 1 s |
| `--servos` | Each servo (1–6) nudges centre → −10° → centre → +10° → centre |
| `--motors` | Robot drives forward then reverses at 30% for 1 s each; without battery, PWM tones only (no movement) — this is a pass |
| Battery ADC | Returns 0 when no battery connected — expected |

## Install

**Systemd services** (button scan, WiFi manager) — installs a bundled venv under `/opt/gogopi/`:

```bash
./packaging/build_deb.sh                       # run on the robot (aarch64 only); requires SSH access to gratzi for rpi-led-proxy
sudo dpkg -i packaging/gogopi-services_*.deb
```

To remove:

```bash
sudo dpkg -r gogopi-services
```

**Python SDK** (for ROS2 nodes and direct SDK use):

```bash
python3 -m venv venv && . venv/bin/activate
pip install -e .
```

## Build and run (ROS2)

```bash
source /opt/ros/jazzy/setup.bash
pip install -e .   # make HiwonderSDK importable to ROS2's Python

cd ros2
colcon build
source install/setup.bash
```

### Session manager — `gprctl`

`gprctl` starts a named tmux session with one window per node and automatically drives all lifecycle transitions:

```bash
./gprctl            # start (or restart) the gopiros2 session
./gprctl stop       # kill the session
./gprctl hwhealth   # run hardware health check in the terminal
```

Tmux windows:

| # | Name | Contents |
|---|---|---|
| 0 | daemons | hardware health check (read-only) |
| 1 | urdf | `robot_state_publisher` |
| 2 | board | `board_node` |
| 3 | sonar | I2C probe at 0x77, then `sonar_node` |
| 4 | thermal | I2C probe at 0x33, then `thermal_node` (MLX90640) |
| 5 | camera | `/dev/video0` check, then `camera_node` |
| 6 | lidar | `/dev/ydlidar` check, then `lidar_node` |
| 7 | shell | configure + activate all nodes, then interactive |

The status bar (bottom of the tmux session) shows hardware health, ROS node states, and battery level, refreshed every 5 s.

`camera_node` requires `python3-opencv` (`sudo apt install python3-opencv`). If cv2 is absent the node logs a clear error and returns `FAILURE` from `on_configure`; all other nodes continue normally.

### Manual lifecycle control

To drive nodes manually instead of using `gprctl`:

```bash
ros2 lifecycle set /board_node configure && ros2 lifecycle set /board_node activate
ros2 lifecycle set /sonar_node configure && ros2 lifecycle set /sonar_node activate
ros2 lifecycle set /thermal_node configure && ros2 lifecycle set /thermal_node activate
ros2 lifecycle set /camera_node configure && ros2 lifecycle set /camera_node activate
```

To run sensors only (no motors):

```bash
ros2 launch gogopi_bringup sensors_only.launch.py
```

## Architecture and topic reference

See [CLAUDE.md](CLAUDE.md) for:
- Full topic / service / parameter tables for `board_node`, `sonar_node`, `thermal_node`, and `camera_node`
- SDK two-phase design (`Board.initialize()` / `Board.cleanup()`)
- WiFi and power-button service semantics
- Custom message definitions (`gogopi_msgs`)

The message definitions themselves are the canonical reference:
- [`ros2/src/gogopi_msgs/msg/`](ros2/src/gogopi_msgs/msg/)
- [`ros2/src/gogopi_msgs/srv/`](ros2/src/gogopi_msgs/srv/)

## Robot Description (URDF)

The robot geometry is described in [`ros2/src/gogopi_bringup/urdf/gogopi.urdf`](ros2/src/gogopi_bringup/urdf/gogopi.urdf), including:
- Base body with 4 wheels (differential drive)
- Sensor tower mounted on the body
- Pan-tilt mechanism at the top (controlled by servos 1 and 2)
- USB and thermal cameras mounted on the tilt link

To visualize or use the robot model:
```bash
ros2 run robot_state_publisher robot_state_publisher --ros-args -p robot_description:="$(cat /opt/ros/jazzy/share/gogopi_bringup/urdf/gogopi.urdf)"
```

Joint names in the URDF match the servo state publishing: `pan_joint` (servo 2), `tilt_joint` (servo 1).
