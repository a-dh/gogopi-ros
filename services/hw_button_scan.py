#!/usr/bin/env python3
"""Button scan service.

KEY1 (BCM 13, BOARD 33) held ~2 s  →  WiFi factory reset
KEY2 (BCM 23, BOARD 16) held ~2 s  →  safe shutdown
Battery monitor: beep 0.25 s every minute while USB-powered (no battery);
5 s buzz before any shutdown (button or battery critical).
`touch /tmp/gogopi-mute` silences the USB beep (not the shutdown buzz); cleared on reboot.
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
BUZZER_PIN = 6  # BCM GPIO 6 / BOARD pin 31, active high
HOLD_COUNTS = 40  # 40 × 50 ms = 2 s

USB_BEEP_INTERVAL    = 60    # seconds between USB-power reminder beeps (and checks)
USB_BEEP_S           = 0.25
MUTE_FILE            = "/tmp/gogopi-mute"  # exists → skip the USB beep
SHUTDOWN_BUZZ_S      = 5.0

pi = None  # pigpio connection, set in __main__

BAT_CHECK_INTERVAL   = 600   # seconds between battery checks
BAT_RECHECK_INTERVAL = 30    # seconds to the confirming check after a critical one
BAT_WARN_V           = 7.2   # 2S LiPo: warn below this (3.6 V/cell)
BAT_SHUTDOWN_V       = 6.8   # 2S LiPo: shutdown below this (3.4 V/cell)
BAT_MIN_PLAUSIBLE    = 5.5   # median below this → no battery (USB-powered)
BAT_SAMPLES          = 10    # readings per check, BAT_SAMPLE_GAP s apart
BAT_SAMPLE_GAP       = 0.1
BAT_MAX_SPREAD       = 0.3   # V; wider max−min → ADC floating (main board switch
                             # off / USB only: readings swing ~4.5–7.5 V)

_bat_critical_count = 0      # consecutive critical checks; shutdown needs 2
_usb_powered = None          # last detected power source; log only on change


def buzz(seconds):
    """Sound the board buzzer for `seconds`; always leaves it off."""
    if pi is None:
        return
    try:
        pi.write(BUZZER_PIN, 1)
        time.sleep(seconds)
    finally:
        pi.write(BUZZER_PIN, 0)


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
    """Sample the battery ADC and act on it. Returns seconds until the next check."""
    global _bat_critical_count, _usb_powered
    samples = []
    for _ in range(BAT_SAMPLES):
        v = _battery_volts()
        if v is not None:
            samples.append(v)
        time.sleep(BAT_SAMPLE_GAP)
    if len(samples) < BAT_SAMPLES // 2:
        log.warning("Battery read failed (I2C error, %d/%d samples)", len(samples), BAT_SAMPLES)
        _bat_critical_count = 0
        return BAT_CHECK_INTERVAL
    samples.sort()
    v = samples[len(samples) // 2]
    spread = samples[-1] - samples[0]
    detail = f"median {v:.2f}V, range {samples[0]:.2f}–{samples[-1]:.2f}V"
    if v < BAT_MIN_PLAUSIBLE or spread > BAT_MAX_SPREAD:
        if _usb_powered is not True:
            log.info("Battery: %s — no battery detected, USB-powered", detail)
        _usb_powered = True
        _bat_critical_count = 0
        if not os.path.exists(MUTE_FILE):
            buzz(USB_BEEP_S)
        return USB_BEEP_INTERVAL
    _usb_powered = False
    if v < BAT_SHUTDOWN_V:
        _bat_critical_count += 1
        if _bat_critical_count < 2:
            log.warning("Battery critical: %s — confirming in %d s", detail, BAT_RECHECK_INTERVAL)
            return BAT_RECHECK_INTERVAL
        msg = (
            f"\n*** BATTERY CRITICAL: {v:.2f} V ***\n"
            f"System is shutting down NOW.\n"
            f"UNPLUG THE BATTERY after poweroff to prevent damage.\n"
        )
        log.error("Battery critical: %s — initiating shutdown", detail)
        subprocess.call(["wall", msg])
        time.sleep(3)
        do_shutdown(log)
        return BAT_CHECK_INTERVAL
    _bat_critical_count = 0
    if v < BAT_WARN_V:
        msg = (
            f"\n*** Battery low: {v:.2f} V ***\n"
            f"Please save work and shut down soon.\n"
            f"Remember to UNPLUG THE BATTERY after poweroff.\n"
        )
        log.warning("Battery low: %s — wall warning sent", detail)
        subprocess.call(["wall", msg])
    else:
        log.info("Battery: %s", detail)
    return BAT_CHECK_INTERVAL


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
    log.info("Shutdown buzzer: %.0f s", SHUTDOWN_BUZZ_S)
    buzz(SHUTDOWN_BUZZ_S)
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
    pi.set_mode(BUZZER_PIN, pigpio.OUTPUT)
    pi.write(BUZZER_PIN, 0)
    logger.info("GPIO configured: KEY1=BCM%d KEY2=BCM%d", KEY1_PIN, KEY2_PIN)

    key1_pressed = False
    key2_pressed = False
    count = 0
    next_bat_check = 0.0  # trigger an immediate check on first iteration

    while True:
        now = time.monotonic()
        if now >= next_bat_check:
            next_bat_check = now + battery_check(logger)

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
