#!/usr/bin/env python3
"""
Output a tmux status-right string showing HW health, ROS connection, and
battery ADC.  Designed to run every few seconds inside tmux status-right
without sourcing ROS2 (would be too slow).

Checks performed — all complete in <50 ms:
  HW health   : systemctl is-active pigpiod + rpi-led-proxy
  ROS          : pgrep for the board_node process (lifecycle proxy)
  Battery      : smbus2 direct read of I2C bus 1 / 0x7A register 0
"""

import subprocess
import sys


# ── helpers ───────────────────────────────────────────────────────────────────

def _svc_active(name: str) -> bool:
    r = subprocess.run(["systemctl", "is-active", name], capture_output=True, text=True)
    return r.stdout.strip() == "active"


def _board_node_running() -> bool:
    r = subprocess.run(["pgrep", "-f", "board_node"], capture_output=True)
    return r.returncode == 0


def _battery_raw() -> int | None:
    try:
        from smbus2 import SMBus, i2c_msg
        with SMBus(1) as bus:
            w = i2c_msg.write(0x7A, [0])     # select register 0 (ADC_BAT)
            r = i2c_msg.read(0x7A, 2)
            bus.i2c_rdwr(w, r)
            d = list(r)
            return d[0] | (d[1] << 8)
    except Exception:
        return None


# ── checks ────────────────────────────────────────────────────────────────────

pigpiod_ok = _svc_active("pigpiod")
led_ok     = _svc_active("rpi-led-proxy")
hw_ok      = pigpiod_ok and led_ok

ros_running = _board_node_running()

bat = _battery_raw()


# ── format ────────────────────────────────────────────────────────────────────

SEP = " #[fg=colour238]│#[default] "

# HW — show which daemon is down if not both OK
if hw_ok:
    hw_str = "#[fg=green]HW ✓#[default]"
else:
    bad = []
    if not pigpiod_ok:
        bad.append("pigpiod")
    if not led_ok:
        bad.append("led-proxy")
    hw_str = f"#[fg=red]HW ✗ ({','.join(bad)})#[default]"

# ROS — process-existence proxy for lifecycle-active
if ros_running:
    ros_str = "#[fg=green]board: run#[default]"
else:
    ros_str = "#[fg=red]board: off#[default]"

# Battery — millivolts → volts with 2S LiPo thresholds
if bat is None:
    bat_str = "#[fg=colour240]BAT --#[default]"
else:
    v = bat / 1000.0
    color = "green" if v >= 7.8 else ("yellow" if v >= 7.2 else "red")
    bat_str = f"#[fg={color}]BAT {v:.2f}V#[default]"

print(" " + SEP.join([hw_str, ros_str, bat_str]) + " ")
