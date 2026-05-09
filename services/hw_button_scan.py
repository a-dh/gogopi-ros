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

    while True:
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
