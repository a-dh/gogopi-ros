#!/usr/bin/env python3
"""Find the pan/tilt servos' mechanical end stops from battery-voltage sag.

A servo pushing against its stop draws stall current, which shows up as a dip
in battery voltage. This steps each servo from centre towards each end and
watches the battery ADC (I2C 0x7A, 1 mV/count) for a sustained jump in sag.

Run ON THE ROBOT with gprctl running (board_node active), a battery connected
and the main board switched on. Close the quattro RViz slider GUI first —
it publishes /joint_states and would fight the sweep.

    tools/pt_range_check.py baseline      # noise floor only (servos centred)
    tools/pt_range_check.py sweep         # find stops (pauses lidar, restores limits)

Nothing is changed permanently: board_node's servo_min_deg/servo_max_deg are
opened to 0–180 for the sweep and always restored afterwards.
"""

import argparse
import statistics
import sys
import time

import rclpy
from rcl_interfaces.msg import Parameter, ParameterType, ParameterValue
from rcl_interfaces.srv import GetParameters, SetParameters
from lifecycle_msgs.msg import Transition
from lifecycle_msgs.srv import ChangeState
from gogopi_msgs.msg import ServoCommand
from gogopi_msgs.srv import SetServo
from smbus2 import SMBus, i2c_msg

ADC_ADDR = 0x7A
BAT_MIN_PLAUSIBLE = 5.5     # V; below → no battery
BAT_MAX_SPREAD = 0.3        # V; wider → ADC floating (no battery), same rule as hw_button_scan

BASELINE_SAMPLES = 100
SAMPLE_HZ = 50.0
STEP_SAMPLES = 20           # readings per sweep step (0.4 s)
SETTLE_S = 0.4              # after each step, before sampling
STEP_DEG = 2
STALL_SIGMA = 4.0           # dip jump over running level, in σ of a STEP_SAMPLES median
BACKOFF_DEG = 10
MAX_STALL_S = 1.0           # never hold a suspected stall longer than this

SERVOS = {1: "tilt", 2: "pan"}


# ── battery ADC ──────────────────────────────────────────────────────────────

def read_volts(bus):
    w = i2c_msg.write(ADC_ADDR, [0])
    r = i2c_msg.read(ADC_ADDR, 2)
    bus.i2c_rdwr(w, r)
    d = list(r)
    return (d[0] | (d[1] << 8)) / 1000.0


def sample(bus, n, hz=SAMPLE_HZ):
    out = []
    period = 1.0 / hz
    for _ in range(n):
        t = time.monotonic()
        try:
            out.append(read_volts(bus))
        except OSError:
            pass  # bus shared with board_node; skip a collided read
        time.sleep(max(0.0, period - (time.monotonic() - t)))
    return out


def stats(v):
    s = sorted(v)
    return {
        "n": len(v),
        "median": statistics.median(s),
        "mean": statistics.fmean(s),
        "stdev": statistics.pstdev(s),
        "min": s[0],
        "max": s[-1],
    }


def check_battery(st):
    if st["median"] < BAT_MIN_PLAUSIBLE or st["max"] - st["min"] > BAT_MAX_SPREAD:
        sys.exit(f"No battery detected (median {st['median']:.3f} V, range "
                 f"{st['min']:.3f}–{st['max']:.3f} V). Connect a battery and switch the main board on.")


# ── ROS helpers ──────────────────────────────────────────────────────────────

class Rig:
    def __init__(self):
        rclpy.init()
        self.node = rclpy.create_node("pt_range_check")
        self.set_servo = self._client(SetServo, "/gogopi/set_servo")
        self.get_params = self._client(GetParameters, "/board_node/get_parameters")
        self.set_params = self._client(SetParameters, "/board_node/set_parameters")

    def _client(self, srv, name, timeout=10.0):
        c = self.node.create_client(srv, name)
        if not c.wait_for_service(timeout_sec=timeout):
            sys.exit(f"Service {name} not available — is gprctl running with board_node active?")
        return c

    def call(self, client, req, timeout=5.0):
        fut = client.call_async(req)
        rclpy.spin_until_future_complete(self.node, fut, timeout_sec=timeout)
        if not fut.done():
            raise RuntimeError(f"service call timed out: {client.srv_name}")
        return fut.result()

    def servo(self, sid, angle):
        cmd = ServoCommand(servo_id=sid, use_pulse=False, angle_deg=int(round(angle)), move_time_ms=0)
        self.call(self.set_servo, SetServo.Request(command=cmd))

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

    def lidar(self, transition_id):
        c = self.node.create_client(ChangeState, "/lidar_node/change_state")
        if not c.wait_for_service(timeout_sec=3.0):
            return False
        res = self.call(c, ChangeState.Request(transition=Transition(id=transition_id)), timeout=15.0)
        return res.success

    def close(self):
        self.node.destroy_node()
        rclpy.shutdown()


def centre(rig):
    rig.servo(1, 90)
    rig.servo(2, 90)
    time.sleep(1.0)


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_baseline(rig, bus):
    print("Centring servos (90°/90°) …")
    centre(rig)
    print(f"Sampling battery: {BASELINE_SAMPLES} readings at {SAMPLE_HZ:.0f} Hz …")
    v = sample(bus, BASELINE_SAMPLES)
    st = stats(v)
    check_battery(st)
    sigma_step = 1.2533 * st["stdev"] / (STEP_SAMPLES ** 0.5)  # σ of a STEP_SAMPLES median
    print(f"  median {st['median']:.3f} V   mean {st['mean']:.3f} V   σ {st['stdev']*1000:.1f} mV")
    print(f"  range  {st['min']:.3f}–{st['max']:.3f} V  ({(st['max']-st['min'])*1000:.0f} mV p-p, n={st['n']})")
    print(f"  σ of a {STEP_SAMPLES}-reading median ≈ {sigma_step*1000:.1f} mV → "
          f"stall threshold {STALL_SIGMA:.0f}σ ≈ {STALL_SIGMA*sigma_step*1000:.1f} mV")
    print("  Expected stall sag ~25–100 mV. Threshold well below that → sweep should work.")
    return st, sigma_step


def sweep_direction(rig, bus, sid, target, sigma_step, base_median):
    step = STEP_DEG if target > 90 else -STEP_DEG
    centre(rig)
    dips = []
    angle = 90
    print(f"\n{SERVOS[sid]} (servo {sid}) 90° → {target}°")
    while (angle + step <= target) if step > 0 else (angle + step >= target):
        angle += step
        rig.servo(sid, angle)
        time.sleep(SETTLE_S)
        t0 = time.monotonic()
        dip = base_median - statistics.median(sample(bus, STEP_SAMPLES))
        level = statistics.median(dips[-5:]) if dips else 0.0
        flag = ""
        if dip - level > STALL_SIGMA * sigma_step:
            # confirm with a second reading, within the stall time budget
            dip2 = base_median - statistics.median(sample(bus, STEP_SAMPLES))
            if dip2 - level > STALL_SIGMA * sigma_step:
                rig.servo(sid, angle - step * (BACKOFF_DEG // STEP_DEG))
                held = time.monotonic() - t0
                print(f"  {angle:4d}°  dip {dip*1000:6.1f} / {dip2*1000:6.1f} mV  ← STALL (held {held:.1f} s, backed off)")
                return angle, dip2
            flag = "  (spike, not confirmed)"
        dips.append(dip)
        print(f"  {angle:4d}°  dip {dip*1000:6.1f} mV{flag}")
    print(f"  no stall up to {target}°")
    return None, None


def cmd_sweep(rig, bus, servos):
    st, sigma_step = cmd_baseline(rig, bus)
    assert 2 * STEP_SAMPLES / SAMPLE_HZ <= MAX_STALL_S + 1e-9
    old_lo, old_hi = rig.get_limits()
    print(f"\nboard_node limits now: min {old_lo} max {old_hi} — opening to 0–180 for the sweep")
    lidar_paused = rig.lidar(Transition.TRANSITION_DEACTIVATE)
    print("lidar paused" if lidar_paused else "lidar not paused (not running?)")
    results = []
    try:
        rig.set_limits([0.0, 0.0], [180.0, 180.0])
        for sid in servos:
            for target in (0, 180):
                stop, dip = sweep_direction(rig, bus, sid, target, sigma_step, st["median"])
                results.append((sid, target, stop, dip))
    finally:
        centre(rig)
        rig.set_limits(old_lo, old_hi)
        print(f"\nlimits restored: min {old_lo} max {old_hi}")
        if lidar_paused:
            rig.lidar(Transition.TRANSITION_ACTIVATE)
            print("lidar re-activated")

    print("\nservo  end   stall at   dip      suggested limit (3° margin)")
    for sid, target, stop, dip in results:
        if stop is None:
            print(f"  {sid} {SERVOS[sid]:4s} {target:3d}°   none       —        {target}°")
        else:
            lim = stop + 3 if target == 0 else stop - 3
            print(f"  {sid} {SERVOS[sid]:4s} {target:3d}°   {stop:3d}°    {dip*1000:5.0f} mV   {lim}°")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["baseline", "sweep"])
    ap.add_argument("--servo", type=int, choices=[1, 2], action="append",
                    help="servo(s) to sweep (default: both; 1=tilt, 2=pan)")
    args = ap.parse_args()

    rig = Rig()
    try:
        with SMBus(1) as bus:
            if args.command == "baseline":
                cmd_baseline(rig, bus)
            else:
                cmd_sweep(rig, bus, args.servo or [1, 2])
    finally:
        rig.close()


if __name__ == "__main__":
    main()
