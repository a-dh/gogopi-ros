#!/usr/bin/env python3
"""Find the pan/tilt servos' mechanical end stops using the camera as feedback.

The camera rides on the pan/tilt head, so while the head moves the picture
shifts (direction depends on how the camera is mounted — on this robot tilt
moves the image sideways — so the total shift magnitude is used). Once the head hits its stop,
further servo commands no longer move the picture. This steps a servo 2° at
a time from centre towards an end, measures the image shift per step with
phase correlation, and calls it a stop when two consecutive steps move less
than STALL_FRACTION of a normal step. It then backs off at once.

(Battery-voltage sag was tried first: a tilt stall only dipped the battery
~8 mV against ~24 mV of ADC noise — not detectable.)

Run ON THE ROBOT with gprctl running (board_node + camera_node active). Close
the quattro RViz slider GUI first — it publishes /joint_states and would
fight the sweep. Point the camera at a scene with detail (not a blank wall).

    tools/pt_range_check.py baseline                 # scene texture + still-image noise
    tools/pt_range_check.py sweep --servo 1 --end low   # one direction (tilt down)
    tools/pt_range_check.py sweep                    # both servos, both ends
    tools/pt_range_check.py sweep --servo 1 --end low --beep-at 14   # beep at 14° to watch
    tools/pt_range_check.py level                    # tilt angle where the camera is level

'level' needs world-vertical edges in view (shelf uprights, door frames, wall
corners) and an upright image (camera_node image_rotation 90 on this robot).
When the camera pitches, vertical edges converge (up → towards the top); at
level they are parallel. It sweeps tilt within board_node's current limits,
measures the convergence in each frame, and finds the zero crossing.

Nothing is changed permanently: board_node's servo_min_deg/servo_max_deg are
opened to 0–180 for the sweep and always restored afterwards.
"""

import argparse
import statistics
import sys
import threading
import time

import cv2
import numpy as np
import rclpy
from rclpy.executors import SingleThreadedExecutor
from rcl_interfaces.msg import Parameter, ParameterType, ParameterValue
from rcl_interfaces.srv import GetParameters, SetParameters
from sensor_msgs.msg import Image
from std_srvs.srv import SetBool
from gogopi_msgs.msg import ServoCommand
from gogopi_msgs.srv import SetServo

STEP_DEG = 2
SETTLE_S = 0.5              # after each step, before grabbing a frame
CALIBRATION_STEPS = 3       # first steps from centre set the "normal" shift per step
STALL_FRACTION = 0.3        # step moving < this × normal → no movement
STALL_STEPS = 2             # consecutive no-movement steps to call a stop
BACKOFF_DEG = 10
MIN_RESPONSE = 0.1          # phase-correlation peak below this → scene lost
MIN_STEP_PX = 1.0           # calibration steps must shift at least this much
WORK_WIDTH = 320            # frames are downscaled to this width for correlation

SERVOS = {1: "tilt", 2: "pan"}
PROBE_STEPS = 3             # baseline: steps per servo to reveal backlash vs. normal steps
# Tilt servo 90° points the camera 45° up; there pan partly *twists* the image
# (pure rotation when pointing straight up), which phase correlation can't see.
# Level the camera for pan: tilt servo 45° (= tilt_joint +45° from its 45°-up zero).
LEVEL_TILT_DEG = 45


class Rig:
    def __init__(self):
        rclpy.init()
        self.node = rclpy.create_node("pt_range_check")
        self._lock = threading.Lock()
        self._frame = None          # (monotonic receive time, grayscale float32 @ WORK_WIDTH)
        self._full = None           # full-resolution grayscale uint8 of the same frame
        self.node.create_subscription(Image, "/gogopi/camera/image_raw", self._on_image, 2)
        self._exec = SingleThreadedExecutor()
        self._exec.add_node(self.node)
        self._spin = threading.Thread(target=self._exec.spin, daemon=True)
        self._spin.start()
        self.set_servo = self._client(SetServo, "/gogopi/set_servo")
        self.get_params = self._client(GetParameters, "/board_node/get_parameters")
        self.set_params = self._client(SetParameters, "/board_node/set_parameters")
        self.set_buzzer = self._client(SetBool, "/gogopi/set_buzzer")

    def _client(self, srv, name, timeout=10.0):
        c = self.node.create_client(srv, name)
        if not c.wait_for_service(timeout_sec=timeout):
            sys.exit(f"Service {name} not available — is gprctl running with board_node active?")
        return c

    def _on_image(self, msg):
        if msg.encoding != "bgr8":
            return
        img = np.frombuffer(bytes(msg.data), np.uint8).reshape(msg.height, msg.width, 3)
        full = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h = int(full.shape[0] * WORK_WIDTH / full.shape[1])
        gray = cv2.resize(full, (WORK_WIDTH, h), interpolation=cv2.INTER_AREA).astype(np.float32)
        with self._lock:
            self._frame = (time.monotonic(), gray)
            self._full = full

    def frame_after(self, t, timeout=3.0, full=False):
        """First frame received after monotonic time t (work-size float, or full-res uint8)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                f, g = self._frame, self._full
            if f is not None and f[0] > t:
                return (f[1], g) if full else f[1]
            time.sleep(0.02)
        sys.exit("No camera frames — is camera_node active?")

    def call(self, client, req, timeout=5.0):
        fut = client.call_async(req)
        deadline = time.monotonic() + timeout
        while not fut.done():
            if time.monotonic() > deadline:
                raise RuntimeError(f"service call timed out: {client.srv_name}")
            time.sleep(0.01)
        return fut.result()

    def servo(self, sid, angle):
        cmd = ServoCommand(servo_id=sid, use_pulse=False, angle_deg=int(round(angle)), move_time_ms=0)
        self.call(self.set_servo, SetServo.Request(command=cmd))

    def beep(self, seconds=0.15):
        self.call(self.set_buzzer, SetBool.Request(data=True))
        time.sleep(seconds)
        self.call(self.set_buzzer, SetBool.Request(data=False))

    def get_limits(self):
        res = self.call(self.get_params, GetParameters.Request(names=["servo_min_deg", "servo_max_deg"]))
        return [list(v.double_array_value) for v in res.values]

    def set_limits(self, lo, hi):
        def p(name, val):
            return Parameter(name=name, value=ParameterValue(
                type=ParameterType.PARAMETER_DOUBLE_ARRAY, double_array_value=[float(x) for x in val]))
        res = self.call(self.set_params, SetParameters.Request(
            parameters=[p("servo_min_deg", lo), p("servo_max_deg", hi)]))
        if not all(r.successful for r in res.results):
            raise RuntimeError(f"setting servo limits failed: {[r.reason for r in res.results]}")

    def close(self):
        self._exec.shutdown()
        self._spin.join(timeout=2.0)
        self.node.destroy_node()
        rclpy.shutdown()


def shift(a, b):
    """Image shift from a to b in pixels (dx, dy) and correlation peak response."""
    win = cv2.createHanningWindow((a.shape[1], a.shape[0]), cv2.CV_32F)
    (dx, dy), resp = cv2.phaseCorrelate(a, b, win)
    return dx, dy, resp


def centre(rig, tilt=90):
    rig.servo(1, tilt)
    rig.servo(2, 90)
    time.sleep(1.5)


def home_for(sid):
    """Tilt to hold while moving servo sid: level camera for pan, centre otherwise."""
    return LEVEL_TILT_DEG if sid == 2 else 90


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_baseline(rig):
    print("Centring servos (90°/90°) …")
    centre(rig)
    frames = []
    for _ in range(6):
        frames.append(rig.frame_after(time.monotonic()))
        time.sleep(0.2)
    res = [shift(frames[0], f) for f in frames[1:]]
    mags = [max(abs(dx), abs(dy)) for dx, dy, _ in res]
    resps = [r for _, _, r in res]
    noise = max(mags)
    print(f"  still-image shift: max {noise:.2f} px, median {statistics.median(mags):.2f} px")
    print(f"  correlation response: median {statistics.median(resps):.2f} (need ≥ {MIN_RESPONSE})")
    if statistics.median(resps) < MIN_RESPONSE:
        sys.exit("  Scene too featureless for shift measurement — point the camera at something with detail.")

    # a few probe steps per servo: shift a normal 2° step produces (a small first
    # step followed by normal ones = backlash/slack in the mount)
    for sid in (1, 2):
        centre(rig, home_for(sid))
        prev = rig.frame_after(time.monotonic())
        for i in range(1, PROBE_STEPS + 1):
            rig.servo(sid, 90 + i * STEP_DEG)
            t = time.monotonic()
            time.sleep(SETTLE_S)
            cur = rig.frame_after(t + SETTLE_S)
            dx, dy, r = shift(prev, cur)
            prev = cur
            step_px = float(np.hypot(dx, dy))
            verdict = "OK" if step_px > 4 * max(noise, 0.25) else "TOO SMALL vs noise"
            print(f"  {SERVOS[sid]} {90 + (i - 1) * STEP_DEG}→{90 + i * STEP_DEG}°: dx {dx:+6.2f} dy {dy:+6.2f} px "
                  f"(resp {r:.2f}) → {step_px:5.2f} px — {verdict}")
        rig.servo(sid, 90)
        time.sleep(SETTLE_S)


def sweep_direction(rig, sid, target, beep_at=()):
    step = STEP_DEG if target > 90 else -STEP_DEG
    centre(rig, home_for(sid))
    print(f"\n{SERVOS[sid]} (servo {sid}) 90° → {target}°")
    prev = rig.frame_after(time.monotonic())
    shifts = []
    still = 0
    angle = 90
    while (angle + step <= target) if step > 0 else (angle + step >= target):
        angle += step
        rig.servo(sid, angle)
        if angle in beep_at:
            rig.beep()
        t = time.monotonic()
        time.sleep(SETTLE_S)
        cur = rig.frame_after(t + SETTLE_S)
        dx, dy, resp = shift(prev, cur)
        prev = cur
        moved = float(np.hypot(dx, dy))
        mark = "  ♪" if angle in beep_at else ""
        if resp < MIN_RESPONSE:
            print(f"  {angle:4d}°  shift {moved:5.2f} px  resp {resp:.2f}  ← low response, scene lost; stopping")
            rig.servo(sid, angle - step * (BACKOFF_DEG // STEP_DEG))
            return None, "scene lost"
        if len(shifts) < CALIBRATION_STEPS:
            shifts.append(moved)
            print(f"  {angle:4d}°  shift {moved:5.2f} px  (calibrating){mark}")
            if len(shifts) == CALIBRATION_STEPS and statistics.median(shifts) < MIN_STEP_PX:
                # can't see normal steps → "no movement" would be meaningless
                rig.servo(sid, 90)
                print(f"  calibration steps only shift {statistics.median(shifts):.2f} px "
                      f"(< {MIN_STEP_PX}) — scene too plain or camera not moving; skipping")
                return None, "no visible movement"
            continue
        normal = statistics.median(shifts)
        if moved < STALL_FRACTION * normal:
            still += 1
            if still >= STALL_STEPS:
                stop = angle - step * STALL_STEPS   # last angle that still moved the head
                rig.servo(sid, angle - step * (BACKOFF_DEG // STEP_DEG))
                print(f"  {angle:4d}°  shift {moved:5.2f} px  ← no movement ×{STALL_STEPS}: stop ≈ {stop}° (backed off)")
                return stop, None
            print(f"  {angle:4d}°  shift {moved:5.2f} px  (no movement {still}/{STALL_STEPS}){mark}")
        else:
            still = 0
            shifts.append(moved)
            print(f"  {angle:4d}°  shift {moved:5.2f} px{mark}")
    print(f"  no stop up to {target}°")
    return None, None


LEVEL_STEP_DEG = 3
LEVEL_RANGE = (17, 60)       # servo 1 angles to sample for 'level'
MAX_LINE_TILT_DEG = 20       # edges within this of image-vertical count as world-vertical
MIN_LINES = 4


def vertical_convergence(gray):
    """Near-vertical edges' convergence sums: k = num/den ≈ tan(pitch)/f (up > 0).
    Returns (num, den, lines) so several frames can be pooled."""
    img = np.clip(gray, 0, 255).astype(np.uint8)
    h, w = img.shape
    edges = cv2.Canny(img, 50, 150)
    segs = cv2.HoughLinesP(edges, 1, np.pi / 180, threshold=40,
                           minLineLength=int(0.15 * h), maxLineGap=5)
    cx = w / 2.0
    num = den = 0.0
    used = []
    for x1, y1, x2, y2 in (segs[:, 0] if segs is not None else []):
        dx, dy = float(x2 - x1), float(y2 - y1)
        if abs(dy) < 1 or np.degrees(np.arctan2(abs(dx), abs(dy))) > MAX_LINE_TILT_DEG:
            continue
        xm = (x1 + x2) / 2.0 - cx
        if abs(xm) < 0.1 * w:         # near the centre the slope carries no pitch information
            continue
        slope = dx / dy               # x change per row; same sign as xm when converging upward
        length = float(np.hypot(dx, dy))
        num += length * slope * xm
        den += length * xm * xm
        used.append((x1, y1, x2, y2))
    return num, den, used


def cmd_level(rig, a_from, a_to, step, frames):
    lo_lim, hi_lim = rig.get_limits()
    a0 = max(a_from, int(np.ceil(lo_lim[0])))
    a1 = min(a_to, int(hi_lim[0]))
    angles = list(range(a0, a1 + 1, step))
    print(f"Level search: tilt {a0}–{a1}° in {step}° steps, {frames} frame(s) per step, pan 90°")
    rig.servo(2, 90)
    rig.servo(1, angles[0] - 3 if angles[0] - 3 >= lo_lim[0] else angles[0])
    time.sleep(1.5)
    rows, prev, px_per_deg, full_w = [], None, [], None
    for a in angles:
        rig.servo(1, a)
        t = time.monotonic()
        time.sleep(SETTLE_S)
        num = den = 0.0
        n = 0
        for i in range(frames):
            g, full = rig.frame_after(t + SETTLE_S, full=True)
            t = time.monotonic()
            full_w = full.shape[1]
            if i == 0:
                if prev is not None:
                    dx, dy, _ = shift(prev, g)
                    px_per_deg.append(float(np.hypot(dx, dy)) / step)
                prev = g
            fn, fd, used = vertical_convergence(full)
            num, den, n = num + fn, den + fd, n + len(used)
        rows.append((a, num / den if den > 0 else float("nan"), n / frames))
    # focal length at full resolution, from the image shift per servo degree (assumes 1°/°)
    ppd = statistics.median(px_per_deg[1:] or px_per_deg) * full_w / WORK_WIDTH
    f = ppd / np.tan(np.radians(1.0))
    print(f"  image moves {ppd:.1f} px/° at full res ({full_w} px wide) → focal length ≈ {f:.0f} px")
    print("  tilt   lines/frame   convergence     pitch")
    for a, k, n in rows:
        ok = n >= MIN_LINES
        pitch = np.degrees(np.arctan(k * f)) if ok else float("nan")
        print(f"  {a:4d}°   {n:6.1f}      {k*1e4:+8.3f}e-4   {pitch:+6.1f}°" + ("" if ok else "  (too few lines)"))
    good = [(a, k) for a, k, n in rows if n >= MIN_LINES]
    if len(good) < 3:
        sys.exit("Too few frames with enough vertical edges — point the camera at shelving / door frames.")
    xa = np.array([a for a, _ in good], float)
    kk = np.array([k for _, k in good], float)
    # zero crossing of k (independent of f): robust linear fit, drop >2.5σ outliers once
    sl, ic = np.polyfit(xa, kk, 1)
    r = kk - (sl * xa + ic)
    keep = np.abs(r) <= 2.5 * r.std() if r.std() > 0 else np.ones_like(r, bool)
    sl, ic = np.polyfit(xa[keep], kk[keep], 1)
    r = kk[keep] - (sl * xa[keep] + ic)
    level = -ic / sl
    # uncertainty of the zero crossing from the fit residuals
    se = r.std() / (abs(sl) * np.sqrt(keep.sum())) if keep.sum() > 2 else float("nan")
    pitch_slope = np.degrees(np.arctan(sl * f))
    where = "inside" if a0 <= level <= a1 else "OUTSIDE (extrapolated)"
    print(f"\n  fit on {keep.sum()}/{len(good)} steps: level at tilt servo ≈ {level:.1f}° ± {se:.1f}°  ({where} {a0}–{a1}°)")
    print(f"  pitch changes ≈ {pitch_slope:.2f}° per servo° (≈1 expected if f and servo are both 1:1)")
    target = int(round(min(max(level, a0), a1)))
    rig.servo(1, target)
    t = time.monotonic()
    time.sleep(SETTLE_S)
    _, full = rig.frame_after(t + SETTLE_S, full=True)
    _, _, used = vertical_convergence(full)
    vis = cv2.cvtColor(full, cv2.COLOR_GRAY2BGR)
    for x1, y1, x2, y2 in used:
        cv2.line(vis, (int(x1), int(y1)), (int(x2), int(y2)), (0, 0, 255), 2)
    cv2.imwrite("/tmp/pt_level.jpg", vis)
    print(f"  camera left at tilt {target}°; frame with the {len(used)} edges used: /tmp/pt_level.jpg")


def cmd_sweep(rig, servos, ends, beep_at=()):
    old_lo, old_hi = rig.get_limits()
    print(f"board_node limits now: min {old_lo} max {old_hi} — opening to 0–180 for the sweep")
    results = []
    try:
        rig.set_limits([0.0, 0.0], [180.0, 180.0])
        for sid in servos:
            for target in ends:
                stop, note = sweep_direction(rig, sid, target, beep_at)
                results.append((sid, target, stop, note))
    finally:
        centre(rig)
        rig.set_limits(old_lo, old_hi)
        print(f"\nlimits restored: min {old_lo} max {old_hi}")

    print("\nservo  end    stop     suggested limit (3° margin)")
    for sid, target, stop, note in results:
        if stop is None:
            print(f"  {sid} {SERVOS[sid]:4s} {target:3d}°   none     {target}°" + (f"  ({note})" if note else ""))
        else:
            lim = stop + 3 if target == 0 else stop - 3
            print(f"  {sid} {SERVOS[sid]:4s} {target:3d}°   {stop:3d}°     {lim}°")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["baseline", "sweep", "level"])
    ap.add_argument("--servo", type=int, choices=[1, 2], action="append",
                    help="servo(s) to sweep (default: both; 1=tilt, 2=pan)")
    ap.add_argument("--end", choices=["low", "high"], action="append",
                    help="end(s) to sweep towards: low = 0°, high = 180° (default: both)")
    ap.add_argument("--from", dest="a_from", type=int, default=LEVEL_RANGE[0], help="level: first tilt angle")
    ap.add_argument("--to", dest="a_to", type=int, default=LEVEL_RANGE[1], help="level: last tilt angle")
    ap.add_argument("--step", type=int, default=LEVEL_STEP_DEG, help="level: tilt step (°)")
    ap.add_argument("--frames", type=int, default=1, help="level: frames pooled per step")
    ap.add_argument("--beep-at", type=int, action="append", default=[], metavar="DEG",
                    help="short buzzer beep when the sweep commands this angle (repeatable)")
    args = ap.parse_args()

    rig = Rig()
    try:
        if args.command == "baseline":
            cmd_baseline(rig)
        elif args.command == "level":
            cmd_level(rig, args.a_from, args.a_to, args.step, args.frames)
        else:
            ends = [{"low": 0, "high": 180}[e] for e in (args.end or ["low", "high"])]
            cmd_sweep(rig, args.servo or [1, 2], ends, set(args.beep_at))
    finally:
        rig.close()


if __name__ == "__main__":
    main()
