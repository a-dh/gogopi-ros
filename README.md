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

## Prerequisites

**System packages**

```bash
sudo apt install pigpio i2c-tools
sudo raspi-config   # Interface Options → I2C → enable
```

**Daemons** — must be running before any SDK or ROS2 node use:

```bash
sudo systemctl enable --now pigpiod
sudo systemctl enable --now rpi-led-proxy   # from rpi-led-proxy .deb
```

`rpi-led-proxy` drives the WS281x NeoPixels via GPIO 12 (PWM0). See its own README for `.deb` build and install instructions.

**Python dependencies** — see [pyproject.toml](pyproject.toml).

## Install

```bash
python3 -m venv venv && . venv/bin/activate
pip install -e .

sudo gogopi-install install   # generate, enable, and start systemd services
```

To remove services:

```bash
sudo gogopi-install remove
```

## Build and run (ROS2)

```bash
source /opt/ros/jazzy/setup.bash
pip install -e .   # make HiwonderSDK importable to ROS2's Python

cd ros2
colcon build
source install/setup.bash

ros2 launch gogopi_bringup robot.launch.py
```

Both nodes start in the **unconfigured** lifecycle state. Transition them manually or via a lifecycle manager:

```bash
ros2 lifecycle set /board_node configure && ros2 lifecycle set /board_node activate
ros2 lifecycle set /sonar_node configure && ros2 lifecycle set /sonar_node activate
```

To run sensors only (no motors):

```bash
ros2 launch gogopi_bringup sensors_only.launch.py
```

## Architecture and topic reference

See [CLAUDE.md](CLAUDE.md) for:
- Full topic / service / parameter tables for `board_node` and `sonar_node`
- SDK two-phase design (`Board.initialize()` / `Board.cleanup()`)
- WiFi and power-button service semantics
- Custom message definitions (`gogopi_msgs`)

The message definitions themselves are the canonical reference:
- [`ros2/src/gogopi_msgs/msg/`](ros2/src/gogopi_msgs/msg/)
- [`ros2/src/gogopi_msgs/srv/`](ros2/src/gogopi_msgs/srv/)
