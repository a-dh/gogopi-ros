# GoPiRos2 Quick Reference

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
| **KEY2** — hold 2 s | Safe shutdown (`sync` + `poweroff`) |

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

```
./gprctl             # start tmux session (all nodes)
./gprctl stop        # kill session
./gprctl hwhealth    # hardware health check
```

### tmux windows
| # | Name | Contents |
|---|---|---|
| 0 | daemons | hardware health check |
| 1 | urdf | robot_state_publisher |
| 2 | board | board_node |
| 3 | sonar | sonar_node |
| 4 | thermal | thermal_node |
| 5 | camera | camera_node |
| 6 | shell | interactive shell + auto lifecycle |

`Ctrl-b <n>` switch window · `Ctrl-b d` detach · `Ctrl-b [` scroll

---

## Key services

| Service | Role |
|---|---|
| `pigpiod` | GPIO daemon — must be running |
| `rpi-led-proxy` | WS281x LED daemon — must be running |
| `hw_wifi` | WiFi setup — runs once at boot, exits when done |
| `hw_button_scan` | KEY1 / KEY2 long-press handler |
