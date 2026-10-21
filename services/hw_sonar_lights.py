#!/usr/bin/env python3
"""
Light the Sonar module's two RGB LEDs as nav lights at boot: port red, starboard green.

Sonar LED 0 = port, LED 1 = starboard (the hat NeoPixels are the other way round).
Same level as the hat at boot: 128 matches the hat at full under rpi-led-proxy's
LED_BRIGHTNESS=128. sonar_node dims them while ROS holds the sonar.
Runs once early in boot; retries while the I2C bus / module comes up.
"""

import sys
import time

from smbus2 import SMBus

I2C_BUS = 1
SONAR_ADDR = 0x77
RGB_MODE_REG = 2       # 0 = manual colours
RGB_REGS = (3, 6)      # LED 0 / LED 1: R, G, B registers follow
LEVEL = 128
NAV_LIGHTS = {0: (LEVEL, 0, 0), 1: (0, LEVEL, 0)}
RETRY_S = 30.0


def main():
    deadline = time.monotonic() + RETRY_S
    while True:
        try:
            with SMBus(I2C_BUS) as bus:
                bus.write_byte_data(SONAR_ADDR, RGB_MODE_REG, 0)
                for idx, rgb in NAV_LIGHTS.items():
                    for off, val in enumerate(rgb):
                        bus.write_byte_data(SONAR_ADDR, RGB_REGS[idx] + off, val)
            print("Sonar nav lights on")
            return 0
        except OSError as exc:
            if time.monotonic() > deadline:
                print(f"Sonar nav lights failed: {exc}", file=sys.stderr)
                return 1
            time.sleep(0.5)


if __name__ == "__main__":
    sys.exit(main())
