#!/usr/bin/env python3
"""Button scan service.

KEY1 (BCM 13, BOARD 33) held ~2 s  →  WiFi factory reset
KEY2 (BCM 23, BOARD 16) held ~2 s  →  safe shutdown
"""

import logging
import logging.handlers
import os
import sys
import time
import pigpio

KEY1_PIN = 13  # BCM GPIO 13 / BOARD pin 33
KEY2_PIN = 23  # BCM GPIO 23 / BOARD pin 16


def reset_wifi():
    os.system("rm /etc/Hiwonder/* -rf > /dev/null 2>&1")
    os.system("systemctl restart hw_wifi.service > /dev/null 2>&1")


if __name__ == "__main__":
    logger = logging.getLogger("hw_button_scan")
    logger.setLevel(logging.DEBUG)
    logger.addHandler(logging.handlers.SysLogHandler(address="/dev/log"))

    pi = pigpio.pi()
    if not pi.connected:
        logger.error("Cannot connect to pigpiod")
        sys.exit(1)

    pi.set_mode(KEY1_PIN, pigpio.INPUT)
    pi.set_mode(KEY2_PIN, pigpio.INPUT)
    pi.set_pull_up_down(KEY1_PIN, pigpio.PUD_UP)
    pi.set_pull_up_down(KEY2_PIN, pigpio.PUD_UP)

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
                else:
                    count += 1
                    if count >= 40:  # 40 × 50 ms = 2 s
                        count = 0
                        key1_pressed = False
                        logger.info("Key 1 long-press: WiFi reset")
                        reset_wifi()
            else:
                count = 0
                key1_pressed = False
        elif pi.read(KEY2_PIN) == 0:
            time.sleep(0.05)
            if pi.read(KEY2_PIN) == 0:
                if not key2_pressed:
                    key2_pressed = True
                    count = 0
                else:
                    count += 1
                    if count >= 40:
                        count = 0
                        key2_pressed = False
                        logger.info("Key 2 long-press: shutdown")
                        os.system("sync")
                        os.system("sudo poweroff -f -p")
            else:
                count = 0
                key2_pressed = False
        else:
            count = 0
            key1_pressed = False
            key2_pressed = False
            time.sleep(0.05)
