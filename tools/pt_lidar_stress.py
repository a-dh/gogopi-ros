#!/usr/bin/env python3
"""Swing the pan servo hard and look for side effects on the rest of the robot.

Phases: idle → wide pan swings → fast pan shake → small tilt moves → fast tilt
shake → idle. During each phase it
records the lidar (/gogopi/lidar/scan), battery voltage and camera frame rate,
and compares every lidar scan against the first idle phase:

  - scan rate and the longest gap between scans (dropouts, stalls)
  - valid points per scan
  - per-bin noise (std of range) on bins that are stable at idle
  - "glitch" bins per scan: > GLITCH_ABS_M / GLITCH_REL from the idle median,
    or flipping between a return and no return
  - which directions the glitches come from (a cable swinging through the
    scan plane would show up as one sector)

Run ON THE ROBOT with gprctl running (board_node, lidar_node, camera_node
active). Close the quattro RViz slider GUI. Keep people out of the lidar's
view during the run — anything moving shows up as glitches.

    tools/pt_lidar_stress.py
"""

import math
import statistics
import threading
import time

import numpy as np
import rclpy
from rclpy.executors import SingleThreadedExecutor
from sensor_msgs.msg import Image, LaserScan
from std_msgs.msg import Float32
from gogopi_msgs.msg import ServoCommand

LEVEL_TILT_DEG = 28
PHASES = [
    # name, seconds, servo (1 tilt / 2 pan), angles cycled, seconds per target
    ("idle-1", 15.0, None, None, None),
    ("pan 15↔165", 20.0, 2, [15, 165], 0.3),
    ("pan 80↔100", 10.0, 2, [80, 100], 0.1),
    ("tilt 22↔34", 15.0, 1, [22, 34], 0.2),
    ("tilt 26↔30", 10.0, 1, [26, 30], 0.1),
    ("idle-2", 15.0, None, None, None),
]
GLITCH_ABS_M = 0.05
GLITCH_REL = 0.03
STABLE_VALID_FRACTION = 0.9   # bins with a return in ≥ this share of idle scans
LIDAR_YAW_DEG = 180.0         # lidar_link is mounted yaw π on base_link (URDF)


class Recorder:
    def __init__(self):
        rclpy.init()
        self.node = rclpy.create_node("pt_lidar_stress")
        self.lock = threading.Lock()
        self.phase = None
        self.scans = {}      # phase → list of (recv time, ranges np.array)
        self.battery = {}    # phase → list of volts
        self.frames = {}     # phase → list of recv times
        self.angles = None
        n = self.node
        n.create_subscription(LaserScan, "/gogopi/lidar/scan", self._on_scan, 10)
        n.create_subscription(Float32, "/gogopi/battery_voltage", self._on_battery, 10)
        n.create_subscription(Image, "/gogopi/camera/image_raw", self._on_image, 2)
        self.servo_pub = n.create_publisher(ServoCommand, "/gogopi/servo_cmd", 10)
        self.ex = SingleThreadedExecutor()
        self.ex.add_node(n)
        self.th = threading.Thread(target=self.ex.spin, daemon=True)
        self.th.start()

    def _add(self, store, item):
        with self.lock:
            if self.phase is not None:
                store.setdefault(self.phase, []).append(item)

    def _on_scan(self, m):
        if self.angles is None:
            self.angles = m.angle_min + m.angle_increment * np.arange(len(m.ranges))
        self._add(self.scans, (time.monotonic(), np.array(m.ranges, dtype=float)))

    def _on_battery(self, m):
        self._add(self.battery, m.data)

    def _on_image(self, m):
        self._add(self.frames, time.monotonic())

    def servo(self, sid, angle):
        self.servo_pub.publish(ServoCommand(servo_id=sid, use_pulse=False,
                                            angle_deg=int(angle), move_time_ms=0))

    def close(self):
        self.ex.shutdown()
        self.th.join(timeout=2.0)
        self.node.destroy_node()
        rclpy.shutdown()


def run_phases(rec):
    rec.servo(1, LEVEL_TILT_DEG)
    rec.servo(2, 90)
    time.sleep(1.5)
    for name, secs, sid, pattern, dwell in PHASES:
        print(f"phase {name:14s} {secs:4.0f} s …", flush=True)
        with rec.lock:
            rec.phase = name
        end = time.monotonic() + secs
        i = 0
        while time.monotonic() < end:
            if pattern:
                rec.servo(sid, pattern[i % len(pattern)])
                i += 1
                time.sleep(dwell)
            else:
                time.sleep(0.1)
        with rec.lock:
            rec.phase = None
        rec.servo(1, LEVEL_TILT_DEG)
        rec.servo(2, 90)
        time.sleep(1.0)   # let the head settle between phases (not recorded)


def robot_deg(lidar_rad):
    return (math.degrees(lidar_rad) + LIDAR_YAW_DEG + 180.0) % 360.0 - 180.0


def analyse(rec):
    ref_scans = np.array([r for _, r in rec.scans.get("idle-1", [])])
    if len(ref_scans) < 5:
        print("Too few idle lidar scans — is lidar_node active?")
        return
    finite = np.isfinite(ref_scans)
    valid_frac = finite.mean(axis=0)
    stable = valid_frac >= STABLE_VALID_FRACTION
    with np.errstate(all="ignore"):
        ref = np.nanmedian(np.where(finite, ref_scans, np.nan), axis=0)
    print(f"\nreference: {len(ref_scans)} idle scans, {stable.sum()} of {len(ref)} bins stable")

    print("\nphase            scans  rate Hz  max gap s  valid pts  noise mm  glitch bins/scan (mean / max)"
          "  battery V (min / mean)  camera fps")
    sector = {}
    for name, secs, *_ in PHASES:
        sc = rec.scans.get(name, [])
        if not sc:
            print(f"  {name:14s}  no scans!")
            continue
        t = np.array([s[0] for s in sc])
        R = np.array([s[1] for s in sc])
        gaps = np.diff(t) if len(t) > 1 else np.array([0.0])
        fin = np.isfinite(R)
        with np.errstate(all="ignore"):
            dev = np.abs(R - ref)
            thr = np.maximum(GLITCH_ABS_M, GLITCH_REL * ref)
            g_range = stable & fin & (dev > thr)
            g_drop = stable & ~fin                          # stable bin lost its return
            g_new = (valid_frac < 0.1) & fin                # return where there was none
            glitch = g_range | g_drop | g_new
            std = np.nanstd(np.where(fin, R, np.nan), axis=0)
        noise_mm = 1000 * np.nanmedian(std[stable]) if stable.any() else float("nan")
        gps = glitch.sum(axis=1)
        sector[name] = glitch.sum(axis=0)
        bat = rec.battery.get(name, [])
        fr = rec.frames.get(name, [])
        bat_s = f"{min(bat):.2f} / {statistics.fmean(bat):.2f}" if bat else "—"
        fps = (len(fr) - 1) / (fr[-1] - fr[0]) if len(fr) > 1 else 0.0
        print(f"  {name:14s} {len(sc):5d}  {len(sc) / secs:6.2f}  {gaps.max():8.2f}  "
              f"{fin.sum(axis=1).mean():8.1f}  {noise_mm:7.1f}  "
              f"{gps.mean():8.2f} / {gps.max():3d}                {bat_s:14s}   {fps:5.1f}")

    # where do the extra glitches come from? compare stress phases with idle-2
    base = sector.get("idle-2")
    for name in [p[0] for p in PHASES if p[3]]:
        if name not in sector or base is None:
            continue
        n_ph = len(rec.scans.get(name, []))
        n_b = max(1, len(rec.scans.get("idle-2", [])))
        excess = sector[name] / max(1, n_ph) - base / n_b      # glitch rate per bin above idle
        hot = np.where(excess > 0.2)[0]                         # bin glitches in >20% more scans
        if hot.size == 0:
            print(f"\n  {name}: no direction with extra glitches (>20% of scans above idle)")
            continue
        # group adjacent bins into sectors
        groups, start = [], hot[0]
        for a, b in zip(hot, hot[1:]):
            if b != a + 1:
                groups.append((start, a))
                start = b
        groups.append((start, hot[-1]))
        print(f"\n  {name}: extra glitches in {hot.size} bins:")
        for a, b in groups:
            print(f"    robot {robot_deg(rec.angles[a]):+6.1f}° … {robot_deg(rec.angles[b]):+6.1f}°"
                  f"  (0° = forward, +90° = port)  ref range {np.nanmedian(ref[a:b + 1]):.2f} m,"
                  f"  glitch rate up to {excess[a:b + 1].max():.0%}")


def main():
    rec = Recorder()
    try:
        time.sleep(1.0)
        run_phases(rec)
        analyse(rec)
    finally:
        rec.servo(2, 90)
        rec.servo(1, LEVEL_TILT_DEG)
        time.sleep(0.3)
        rec.close()


if __name__ == "__main__":
    main()
