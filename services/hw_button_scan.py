#!/usr/bin/env python3
"""Button scan service.

KEY1 (BCM 13, BOARD 33) held ~2 s  →  WiFi factory reset
KEY2 (BCM 23, BOARD 16) held ~2 s  →  safe shutdown
"""

import logging
import logging.handlers
import os
import subprocess
import sys
import time
import pigpio

KEY1_PIN = 13  # BCM GPIO 13 / BOARD pin 33
KEY2_PIN = 23  # BCM GPIO 23 / BOARD pin 16
HOLD_COUNTS = 40  # 40 × 50 ms = 2 s

BAT_CHECK_INTERVAL = 600   # seconds between battery readings
BAT_WARN_V         = 7.2   # 2S LiPo: warn below this (3.6 V/cell)
BAT_SHUTDOWN_V     = 6.8   # 2S LiPo: shutdown below this (3.4 V/cell)
BAT_MIN_PLAUSIBLE  = 5.5   # below this → no battery (USB-powered), skip monitoring


def _battery_volts():
    try:
        from smbus2 import SMBus, i2c_msg
        with SMBus(1) as bus:
            w = i2c_msg.write(0x7A, [0])
            r = i2c_msg.read(0x7A, 2)
            bus.i2c_rdwr(w, r)
            d = list(r)
            return (d[0] | (d[1] << 8)) / 1000.0
    except Exception:
        return None


def battery_check(log):
    v = _battery_volts()
    if v is None:
        log.warning("Battery read failed (I2C error)")
        return
    if v < BAT_MIN_PLAUSIBLE:
        log.info("Battery: %.2fV — no battery detected, USB-powered", v)
        return
    if v < BAT_SHUTDOWN_V:
        msg = (
            f"\n*** BATTERY CRITICAL: {v:.2f} V ***\n"
            f"System is shutting down NOW.\n"
            f"UNPLUG THE BATTERY after poweroff to prevent damage.\n"
        )
        log.error("Battery critical: %.2fV — initiating shutdown", v)
        subprocess.call(["wall", msg])
        time.sleep(3)
        do_shutdown(log)
    elif v < BAT_WARN_V:
        msg = (
            f"\n*** Battery low: {v:.2f} V ***\n"
            f"Please save work and shut down soon.\n"
            f"Remember to UNPLUG THE BATTERY after poweroff.\n"
        )
        log.warning("Battery low: %.2fV — wall warning sent", v)
        subprocess.call(["wall", msg])
    else:
        log.info("Battery: %.2fV", v)


def setup_logging():
    log = logging.getLogger("hw_button_scan")
    log.setLevel(logging.DEBUG)
    # stderr → captured by systemd journal (journalctl -u hw_button_scan)
    sh = logging.StreamHandler(sys.stderr)
    sh.setFormatter(logging.Formatter("%(name)s: %(levelname)s: %(message)s"))
    log.addHandler(sh)
    # also send to syslog if available
    try:
        log.addHandler(logging.handlers.SysLogHandler(address="/dev/log"))
    except OSError:
        pass
    return log


def run(cmd, log):
    log.info("Running: %s", cmd)
    rc = subprocess.call(cmd, shell=True)
    if rc != 0:
        log.error("Command failed (rc=%d): %s", rc, cmd)
    return rc


def reset_wifi(log):
    run("rm -rf /etc/Hiwonder/", log)
    run("systemctl restart hw_wifi.service", log)


def do_shutdown(log):
    log.info("Syncing filesystems")
    run("sync", log)
    log.info("Issuing poweroff via systemctl")
    run("systemctl poweroff", log)


if __name__ == "__main__":
    logger = setup_logging()
    logger.info("Starting hw_button_scan")

    pi = pigpio.pi()
    if not pi.connected:
        logger.error("Cannot connect to pigpiod")
        sys.exit(1)

    pi.set_mode(KEY1_PIN, pigpio.INPUT)
    pi.set_mode(KEY2_PIN, pigpio.INPUT)
    pi.set_pull_up_down(KEY1_PIN, pigpio.PUD_UP)
    pi.set_pull_up_down(KEY2_PIN, pigpio.PUD_UP)
    logger.info("GPIO configured: KEY1=BCM%d KEY2=BCM%d", KEY1_PIN, KEY2_PIN)

    key1_pressed = False
    key2_pressed = False
    count = 0
    last_bat_check = 0.0  # trigger an immediate check on first iteration

    while True:
        now = time.monotonic()
        if now - last_bat_check >= BAT_CHECK_INTERVAL:
            battery_check(logger)
            last_bat_check = now

        if pi.read(KEY1_PIN) == 0:
            time.sleep(0.05)
            if pi.read(KEY1_PIN) == 0:
                if not key1_pressed:
                    key1_pressed = True
                    count = 0
                    logger.debug("KEY1 press detected, holding for WiFi reset...")
                else:
                    count += 1
                    if count % 10 == 0:
                        logger.debug("KEY1 hold count=%d/%d", count, HOLD_COUNTS)
                    if count >= HOLD_COUNTS:
                        count = 0
                        key1_pressed = False
                        logger.info("KEY1 long-press confirmed: triggering WiFi reset")
                        reset_wifi(logger)
            else:
                if key1_pressed:
                    logger.debug("KEY1 released at count=%d (need %d for action)", count, HOLD_COUNTS)
                count = 0
                key1_pressed = False
        elif pi.read(KEY2_PIN) == 0:
            time.sleep(0.05)
            if pi.read(KEY2_PIN) == 0:
                if not key2_pressed:
                    key2_pressed = True
                    count = 0
                    logger.debug("KEY2 press detected, holding for shutdown...")
                else:
                    count += 1
                    if count % 10 == 0:
                        logger.debug("KEY2 hold count=%d/%d", count, HOLD_COUNTS)
                    if count >= HOLD_COUNTS:
                        count = 0
                        key2_pressed = False
                        logger.info("KEY2 long-press confirmed: triggering shutdown")
                        do_shutdown(logger)
            else:
                if key2_pressed:
                    logger.debug("KEY2 released at count=%d (need %d for action)", count, HOLD_COUNTS)
                count = 0
                key2_pressed = False
        else:
            count = 0
            key1_pressed = False
            key2_pressed = False
            time.sleep(0.05)
