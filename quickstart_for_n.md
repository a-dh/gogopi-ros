# Quickstart for N

Welcome! This is a short guide to the **gopiros2** project: what it is, how the
pieces fit, how to get it running, and how to work on it with Claude (or another
AI coding assistant). It's written for someone who knows some Linux and Python
but is new to this robot and to AI-assisted coding. There's also a section on
the usual network problems when working from mainland China.

The other docs go deeper:

| File | What it's for |
|---|---|
| [README.md](README.md) | Wiring, I2C layout, install and build |
| [CHEATSHEET.md](CHEATSHEET.md) | Day-to-day operation: buttons, LEDs, buzzer, servos, `gprctl` |
| [CLAUDE.md](CLAUDE.md) | The full technical reference: every node, topic, parameter. Claude Code reads it automatically, and it's useful for people too |

---

## 1. What is this?

**GoGoPi** is a small 4-wheel differential-drive robot (a Hiwonder kit) with a
Raspberry Pi as its brain. This repository turns it into a **ROS 2 Jazzy**
robot. Each sensor and actuator becomes a ROS node that publishes and subscribes
to topics, so you can view everything in RViz, record it, and write your own
behaviours on top.

The hardware:

- **Hiwonder expansion board** on the Pi: 4 motors, 2 servos (camera pan and tilt),
  a buzzer, 2 buttons (KEY1, KEY2), 2 plain LEDs, and RGB "NeoPixels"
- **Ultrasonic sonar** (Hiwonder "Glowy" module, with 2 RGB LEDs of its own)
- **USB camera** and **MLX90640 thermal camera**, both on the pan/tilt head
- **YDLidar TX8** 360° laser scanner
- **2S LiPo battery** (7.4 V nominal)

Hiwonder and YDLidar are both Shenzhen companies, so replacement parts are easy
to find on Taobao / JD.

### The layers

```
 ┌──────────────────────────────────────────────────────────┐
 │  Desktop PC: RViz in Docker (quattro/)  ← view the robot │
 └───────────────────────────▲──────────────────────────────┘
                             │ ROS 2 topics over WiFi (DDS)
 ┌───────────────────────────┴──────────────────────────────┐
 │  Raspberry Pi                                            │
 │                                                          │
 │  ROS 2 nodes (ros2/src/gogopi_bringup)  ← started by     │
 │    board_node  sonar_node  camera_node     ./gprctl      │
 │    thermal_node  lidar_node  robot_state_publisher       │
 │                    │                                     │
 │  HiwonderSDK (Board.py, Sonar.py)  ← plain Python driver │
 │                    │                                     │
 │  System daemons: pigpiod (GPIO), rpi-led-proxy (LEDs)    │
 │                                                          │
 │  systemd services (services/), independent of ROS:       │
 │    hw_wifi         WiFi access point / client, LED1      │
 │    hw_button_scan  KEY1 = WiFi reset, KEY2 = shutdown,   │
 │                    battery monitor, LED2, buzzer         │
 │    hw_sonar_lights sonar LEDs at boot                    │
 └──────────────────────────────────────────────────────────┘
```

### Ideas worth knowing first

- **Lifecycle nodes.** Every ROS node here starts *unconfigured* and does
  nothing until it's told to `configure` and then `activate`. `./gprctl` does that
  for you. If a node seems silent, check its state first:
  `ros2 lifecycle get /board_node`.
- **The SDK has no side effects on import.** `Board.initialize()` grabs the
  hardware and `Board.cleanup()` releases it. Keep it that way if you change
  `Board.py`.
- **LED ownership.** LED1 belongs to `hw_wifi` and LED2 to `hw_button_scan`.
  ROS nodes only *read* them and only write the RGB LEDs.
- **Servo limits are there to protect hardware.** Tilt is limited to 17–90° and
  pan (for now) to 40–140°. Wider pan swings already snapped the camera's USB
  wire once. Don't widen the limits unless the cables have been rerouted.
- **Nautical nav lights.** Red = port (left), green = starboard (right), on both
  the hat and the sonar.

---

## 2. Repository tour

```
gopiros2/
├── HiwonderSDK/        Hardware driver (pip package "gogopi-sdk")
│   ├── Board.py        motors, servos, buzzer, NeoPixels, buttons, battery
│   │                   (run it directly for a self-test, see README)
│   └── Sonar.py        ultrasonic distance + sonar LEDs
├── ros2/src/
│   ├── gogopi_msgs/    custom messages: PixelArray, ServoCommand, SetServo
│   └── gogopi_bringup/ the ROS nodes, launch files, URDF robot model
├── services/           systemd services (WiFi, buttons, battery) + hw_check.py
├── packaging/          builds the gogopi-services .deb for those services
├── quattro/            Docker image that runs RViz on a desktop PC
├── tools/              calibration / stress-test scripts (run on the robot)
├── config/             DDS (ROS networking) config
├── gprctl              starts everything in a tmux session
└── CLAUDE.md, README.md, CHEATSHEET.md
```

Good first files to read: `ros2/src/gogopi_bringup/gogopi_bringup/sonar_node.py`
(the smallest node, and a clean example of the lifecycle pattern), then
`board_node.py`.

---

## 3. Things that are specific to Albert's setup

Some parts of the repo assume Albert's home network and username. You'll need
to change them, so read this before installing.

| What | Where | What to do |
|---|---|---|
| **`rpi-led-proxy` is on a private server** (`gratzi`), not on GitHub | `pyproject.toml`, `packaging/build_deb.sh` | `pip install -e .` and `build_deb.sh` will fail without it, and `Board.py` imports `wsled_client` from it. **Ask Albert for a copy** (or for it to be published on GitHub), then point both files at the new location |
| Tailscale VPN peer IP `100.77.219.91` | `config/fastdds_tailscale.xml`, `quattro/fastdds_tailscale.xml` | If you don't use Tailscale, ROS discovery on the local network works without it: in `gprctl` set `RMW_FASTRTPS_DEFAULT_PROFILES_FILE=` (empty). `quattro/run.sh` already falls back to multicast when Tailscale isn't running |
| WiFi country code `US` | `services/hw_wifi.py` | Put `HW_WIFI_COUNTRY = "CN"` in your `hiwonder_wifi_conf.py` (see CHEATSHEET.md) so the radio uses the channels allowed in China |
| Hostnames `gopiros2`, `quattro`, `gratzi` | docs and scripts | Albert's machines. Substitute your own |

---

## 4. Working from mainland China

Most of the setup downloads things from servers that are slow or blocked from
mainland China. Domestic mirrors fix most of it.

### Getting the code

- `github.com` works but is often slow or drops mid-clone. Retry, or use a
  shallow clone: `git clone --depth 1 https://github.com/<user>/gopiros2.git`.
- **Gitee** (gitee.com) can import a public GitHub repo. Its "Import from
  GitHub" option makes a fast domestic copy you can clone from and resync.
- Or download the ZIP from the GitHub web page.

### Mirrors (Tsinghua TUNA shown; USTC and Aliyun work the same way)

**Ubuntu apt** (the Pi is arm64, so it uses `ubuntu-ports`): in
`/etc/apt/sources.list.d/ubuntu.sources`, replace `http://ports.ubuntu.com/ubuntu-ports`
with `https://mirrors.tuna.tsinghua.edu.cn/ubuntu-ports`.

**ROS 2 apt:** when following the official ROS 2 Jazzy install guide, use
`https://mirrors.tuna.tsinghua.edu.cn/ros2/ubuntu` in place of
`http://packages.ros.org/ros2/ubuntu`. The GPG key URL is on
`raw.githubusercontent.com`, which is often blocked, so download the key on a
machine that can reach it, or use the copy the TUNA mirror help page points to.

**pip:**
```bash
pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple
```

**rosdep** (if you use it):
```bash
export ROSDISTRO_INDEX_URL=https://mirrors.tuna.tsinghua.edu.cn/rosdistro/index-v4.yaml
```

**YDLidar SDK:** cloned from GitHub (see CLAUDE.md). If GitHub is slow, use a
Gitee mirror or copy it over on a USB stick.

**Docker Hub** (for the RViz container, `osrf/ros:jazzy-desktop`) is unreliable
from China, and public registry mirrors come and go. Check what works now
(Aliyun Container Registry gives each account its own accelerator URL). You
can also skip Docker and install `ros-jazzy-desktop` directly on an Ubuntu 24.04
PC, then run `rviz2` with `quattro/rviz2_conf/default.rviz`.

---

## 5. Getting it running

Follow the order in README.md / CLAUDE.md. In short:

1. **Pi OS:** Ubuntu 24.04 (arm64) + ROS 2 Jazzy (`ros-jazzy-ros-base`), with I2C enabled.
2. **System packages:** `pigpio i2c-tools python3-opencv`, and the extra ROS
   packages listed in CLAUDE.md.
3. **Daemons:** `pigpiod` and `rpi-led-proxy` must be running (see section 3 about the proxy).
4. **SDK:** `pip3 install --user --break-system-packages -e .` (ROS uses the
   system Python, not a venv).
5. **YDLidar SDK:** build and install once (commands in CLAUDE.md).
6. **Services:** `./packaging/build_deb.sh` on the Pi, then `sudo dpkg -i ...`.
7. **Build ROS:**
   ```bash
   source /opt/ros/jazzy/setup.bash
   cd ros2 && colcon build && source install/setup.bash
   ```
8. **Check the hardware:** `./gprctl hwhealth`, then the SDK self-test
   `/usr/bin/python3 HiwonderSDK/Board.py` (LEDs, buzzer, no movement).
9. **Run:** `./gprctl` opens a tmux session with every node and activates them.
   `Ctrl-b <number>` switches windows and `Ctrl-b d` detaches.

### Safety

- **Put the robot on a box with its wheels off the ground** the first time
  anything sends motor commands (`Board.py --motors`, `cmd_vel`).
- `ros2 launch gogopi_bringup sensors_only.launch.py` runs the sensors without motors.
- Emergency stop: `ros2 service call /gogopi/e_stop std_srvs/srv/Trigger`.
- **Shutting down:** hold KEY2 until the buzzer sounds, wait for the Pi to
  stop, *then* switch off the main power. The expansion board keeps draining
  the LiPo otherwise.
- LiPo: never run below ~6.8 V (the service shuts down automatically). Charge
  with a proper balance charger.

### Try a few things

```bash
ros2 topic list
ros2 topic echo /gogopi/sonar/range
ros2 topic echo /gogopi/battery_voltage
ros2 service call /gogopi/set_servo gogopi_msgs/srv/SetServo \
  "{command: {servo_id: 2, use_pulse: false, angle_deg: 110.0}}"   # pan to port
```

---

## 6. Working with Claude Code (or another AI assistant)

This project was built mostly through conversations with **Claude Code**, an
AI assistant that runs in the terminal, reads the repo, edits files and runs
commands with your approval.

**Availability:** Anthropic does not offer Claude in mainland China (see
Anthropic's list of supported countries). Check what's allowed where you are
before relying on it. Everything below also applies to other AI coding
assistants: `CLAUDE.md` is plain Markdown, and most tools can be told to "read
CLAUDE.md first".

### Why CLAUDE.md matters

Claude Code loads `CLAUDE.md` at the start of every session. It's the
project's long-term memory: architecture, hardware facts, decisions and their
reasons (for example *why* pan is limited to 40–140°). **When you learn
something important about the robot, add it to CLAUDE.md.** The assistant (and
any human reader) will then know it next time.

### Tips for beginners

- **Ask before changing.** Start with questions: "Explain how board_node turns
  `cmd_vel` into motor speeds", or "What happens when I hold KEY2?"
- **Plan first for bigger changes.** Ask for a plan (Claude Code has a *plan
  mode*: press `Shift+Tab`), read it, then let it implement.
- **Commit before and after.** `git commit` before letting the AI change things.
  Then `git diff` shows exactly what it did and `git restore` undoes it.
- **Be specific about hardware.** Tell it what you saw ("the tilt servo buzzes
  at 15°") and what's connected. It can't see the robot.
- **Say when you're on the robot vs. the desktop.** Some scripts only run on
  the Pi (aarch64).
- **Review commands that move things or need `sudo`.** You approve each one, so
  read it first, especially motor commands, `dpkg`, or `systemctl`.
- **Useful commands in Claude Code:** `/help`, `/init` (creates a CLAUDE.md for a
  new project), `/clear` (start a fresh conversation), `Esc` to interrupt.

### Example prompts to start with

- "Read CLAUDE.md and give me a 10-line summary of how this robot software is organised."
- "I don't have the rpi-led-proxy package. What breaks, and what's the smallest change that lets Board.py run without it?"
- "Walk me through what ./gprctl does, window by window."
- "Write a new lifecycle node that subscribes to /gogopi/sonar/range and stops the motors when something is closer than 20 cm. Follow the style of sonar_node.py."

---

## 7. Mini glossary

| Term | Meaning |
|---|---|
| **ROS 2 / Jazzy** | Robot Operating System 2; Jazzy is the 2024 LTS release (Ubuntu 24.04) |
| **Node / topic / service** | Program / named data stream (pub-sub) / request-reply call |
| **Lifecycle node** | Node with explicit states: unconfigured → inactive → active |
| **URDF / TF** | XML robot model / the tree of coordinate frames computed from it |
| **RViz** | 3D viewer for ROS data |
| **DDS** | The networking layer that ROS 2 uses to find nodes and move data |
| **pigpiod** | Daemon that gives programs access to the Pi's GPIO pins |
| **NeoPixel / WS281x** | Addressable RGB LEDs |
| **Port / starboard** | Left / right (when facing forward) |
| **2S LiPo** | Two-cell lithium-polymer battery, 6.0–8.4 V |

Have fun, and send Albert questions (or pull requests)!
