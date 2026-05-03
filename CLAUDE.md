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
├── pyproject.toml         ← gogopi-sdk pip package (HiwonderSDK)
└── Makefile               ← install/remove systemd services
```

## Commands

```bash
# Set up Python environment (run from repo root)
python3 -m venv venv && . venv/bin/activate
pip install -e .                       # install gogopi-sdk (HiwonderSDK)

# Install systemd services
sudo make install
sudo make remove

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
- GPIO via pigpiod: buzzer (BCM 6), LED1 (BCM 16), LED2 (BCM 26), KEY1 (BCM 13), KEY2 (BCM 23)
- WS281x via `rpi-led-proxy` Unix socket (`LEDClient` from the `rpi-led-proxy` package)

**NeoPixels:** `setPixelColor(index, r, g, b)` buffers; `show()` sends all pixels to the `led-proxy` daemon at 47% brightness (`__RGB_BRIGHTNESS = 120/255`).

### Sonar.py — stateless per-call

Uses `smbus2` with `with SMBus(...) as bus:` per call. No `initialize()`/`cleanup()` needed. `getDistance()` returns mm (capped at 5000). The Sonar module also carries 2 NeoPixels controlled via its own I2C registers (not the led-proxy).

## ROS2 node architecture

Both nodes use **ROS2 lifecycle** (`LifecycleNode`). Nodes start in unconfigured state; an operator or a launch manager must drive transitions.

### board_node — `gogopi_bringup/board_node.py`

`on_configure` calls `Board.initialize()`. `on_activate` starts publish timers. `on_deactivate` stops motors and cancels timers. `on_cleanup`/`on_shutdown` calls `Board.cleanup()`.

| Topic / Service | Type | Direction |
|---|---|---|
| `/gogopi/cmd_vel` | `geometry_msgs/Twist` | SUB → differential drive |
| `/gogopi/motors_raw` | `std_msgs/Int16MultiArray` | SUB → direct per-motor |
| `/gogopi/servo_cmd` | `gogopi_msgs/ServoCommand` | SUB |
| `/gogopi/board_pixels` | `gogopi_msgs/PixelArray` | SUB |
| `/gogopi/buzzer` | `std_msgs/Bool` | SUB |
| `/gogopi/led1` / `led2` | `std_msgs/Bool` | SUB |
| `/gogopi/battery_raw` | `std_msgs/UInt16` | PUB 2 Hz |
| `/gogopi/buttons` | `sensor_msgs/Joy` | PUB 50 Hz |
| `/gogopi/servo_state` | `sensor_msgs/JointState` | PUB 10 Hz |
| `/gogopi/motor_state` | `std_msgs/Float32MultiArray` | PUB 10 Hz |
| `/gogopi/set_servo` | `gogopi_msgs/srv/SetServo` | SRV |
| `/gogopi/set_buzzer` | `std_srvs/SetBool` | SRV |
| `/gogopi/e_stop` | `std_srvs/Trigger` | SRV |

`cmd_vel` kinematic conversion: `left = v − ω·d/2`, `right = v + ω·d/2`, scaled by `max_speed_pct`. Parameters: `wheel_separation_m` (default 0.15), `max_speed_pct` (default 80).

`battery_raw` publishes raw ADC counts — voltage conversion factor is undocumented and must be measured before using `sensor_msgs/BatteryState`.

### sonar_node — `gogopi_bringup/sonar_node.py`

`on_configure` creates `Sonar()` and sets RGB mode 0. `on_activate` starts 10 Hz range timer.

| Topic | Type | Direction |
|---|---|---|
| `/gogopi/sonar/range` | `sensor_msgs/Range` | PUB 10 Hz |
| `/gogopi/sonar/pixels` | `gogopi_msgs/PixelArray` | SUB |
| `/gogopi/sonar/rgb_mode` | `std_msgs/UInt8` | SUB |

## Services and WiFi/power semantics

**hw_button_scan** (systemd service, uses pigpiod):
- KEY1 (BCM 13) held 2 s → `systemctl restart hw_wifi.service` (WiFi factory reset)
- KEY2 (BCM 23) held 2 s → `sync` + `sudo poweroff -f -p`
- Must start after `pigpiod.service` (declared in service file)

**hw_wifi** (systemd service, uses pigpiod):
- Controls LED1 (BCM 16, active-low) via pigpiod to indicate WiFi state:
  - AP mode starting: fast blink (50 ms on / 50 ms off)
  - Client mode connecting: very fast blink (5 ms / 5 ms)
  - Client connected: solid on
- WiFi config in `/etc/Hiwonder/hiwonder_wifi_conf.py` or `/boot/hiwonder_wifi_conf.py`
- **LED1 ownership**: `hw_wifi` and `board_node` both write LED1 through pigpiod. `hw_wifi` exits once WiFi is set up; `board_node` then takes ownership. Start `board_node` after WiFi is established.

## gogopi_msgs custom types

- **`PixelArray.msg`**: `uint8[] index` + `std_msgs/ColorRGBA[] color` (float 0-1 per channel). Equal-length arrays. Used for both board and sonar NeoPixels.
- **`ServoCommand.msg`**: `servo_id`, `use_pulse` flag, `pulse_us` (500-2500), `angle_deg` (0-180), `move_time_ms`.
- **`SetServo.srv`**: wraps `ServoCommand`, returns `bool success` + `string message`.
