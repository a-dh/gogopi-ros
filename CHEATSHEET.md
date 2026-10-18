# GoPiRos2 Quick Reference

## Power off

**Hold KEY2 for 2 s** → safe shutdown (`hw_button_scan` runs `systemctl poweroff`).
From a shell: `sudo poweroff`.
Unplug the battery once the Pi has shut down — the expansion board stays powered.

---

## Network

| | |
|---|---|
| Tailscale | `gopiros2.tail696671.ts.net` |
| Local hostname | `gopiros2.local` |
| SSH | `ssh albertd@gopiros2.tail696671.ts.net` |

### WiFi fallback AP (no config file present)
| | |
|---|---|
| SSID | `HW-10000000`  (no password) |
| Gateway / SSH | `192.168.149.1` |

### WiFi config file
Place `hiwonder_wifi_conf.py` at `/etc/Hiwonder/` or `/boot/` to set client or AP mode.
Remove the file (or hold KEY1) to reset to default AP.

### Network checks
No NetworkManager (`nmcli` is not installed). Networking is netplan/systemd-networkd, and `hw_wifi` drives hostapd/wpa_supplicant directly.

```
ip -br addr                       # interfaces + IPs at a glance
networkctl status wlan0           # link state, addresses, gateway, DNS
iw dev wlan0 link                 # associated SSID, signal, bitrate
iw dev wlan0 info                 # mode: managed (client) or AP
sudo wpa_cli -i wlan0 status      # client mode: wpa_state=COMPLETED when connected
pgrep -a 'hostapd|dnsmasq|wpa_supplicant'   # which WiFi mode is running
ip route                          # default gateway
resolvectl status                 # DNS servers
tailscale status                  # tailnet peers / connectivity
journalctl -u hw_wifi -b          # hw_wifi log for this boot
```

---

## Buttons

| Button | Action |
|---|---|
| **KEY1** — hold 2 s | Restart `hw_wifi` service (WiFi factory reset) |
| **KEY2** — hold 2 s | Safe shutdown (`systemctl poweroff`) |

---

## LEDs

### LED1 — WiFi status (hw_wifi owns it until WiFi is up, then board_node takes over)

| Pattern | Meaning |
|---|---|
| Fast blink (10 Hz) | AP mode starting |
| Very fast blink (100 Hz) | Client mode connecting |
| Solid on | Connected (or no config — existing WiFi left as-is) |

### LED2 — controlled by board_node

---

## Servos (camera pan-tilt)

| Servo | Joint | Range |
|---|---|---|
| **1** | Tilt (up/down) | 0°–180°, centre = 90° |
| **2** | Pan (left/right) | 0°–180°, centre = 90° |

ROS topics: `/gogopi/servo_cmd`  (ServoCommand msg)
ROS service: `/gogopi/set_servo`  (SetServo srv)

---

## Session control

`gprctl` (repo root) runs every node in its own window of one tmux session, then configures + activates them all — no manual `ros2 lifecycle set` needed.

```
./gprctl [name]        # (re)start tmux session (default name: gopiros2) and attach
./gprctl stop [name]   # kill the session (stops all nodes)
./gprctl hwhealth      # hardware health check only (services/hw_check.py)
```

- Starting always kills any existing session of that name first, so `./gprctl` = restart everything.
- Each window sources ROS2 + the workspace, sets `PYTHONPATH`, the Tailscale FastDDS profile and `ROS_DOMAIN_ID=0`.
- Sensor windows probe their hardware first (I2C 0x33, `/dev/video0`, `/dev/ydlidar`) and print a WARNING if it's missing; that node then fails to configure, and the others carry on.
- The `shell` window waits ~4 s, then sends configure → activate to every node; a node that fails doesn't block the others.
- Status bar (right side) refreshes every 5 s from `services/tmux_status.py`.
- Reattach after detaching: `tmux attach -t gopiros2`.

### tmux windows
| # | Name | Contents |
|---|---|---|
| 0 | daemons | hardware health check |
| 1 | urdf | robot_state_publisher |
| 2 | board | board_node (selected on attach) |
| 3 | thermal | thermal_node |
| 4 | camera | camera_node |
| 5 | lidar | lidar_node |
| 6 | shell | interactive shell + auto lifecycle |

sonar_node is not started (module not connected).

`Ctrl-b <n>` switch window · `Ctrl-b d` detach · `Ctrl-b [` scroll (`q` exits)

Mouse mode is on: wheel scrolls back through output (wheel down to the bottom or `q` to exit), click a window name to switch. Hold **Shift** while dragging to select/copy text with the terminal.

---

## Key services

| Service | Role |
|---|---|
| `pigpiod` | GPIO daemon — must be running |
| `rpi-led-proxy` | WS281x LED daemon — must be running |
| `hw_wifi` | WiFi setup — runs once at boot, exits when done |
| `hw_button_scan` | KEY1 / KEY2 long-press handler |
