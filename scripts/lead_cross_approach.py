#!/usr/bin/env python3
"""Headless closed-loop check for the lead's pre-intersection steering jerk.

The real bot "jerks to the right just before CROSS_STRAIGHT, then goes forward":
the intersection's stop-line + exit markings entering the lane band throw a
one-frame steering spike, and the motor mixer (steer clamped to base_speed, and
base_speed scaled DOWN by raw steer) turns it into a hard right wheel
differential, then a snap to straight when the cross commands steer=0.

This feeds a synthetic approach trace (steer ramps up to a right spike, then the
red line fires the straight cross) through the REAL LeadFSM + motor mixer, with
the steering smoother ENABLED vs DISABLED, and prints the per-frame wheel
differential so the jerk and its smoothing are visible. dt is jittered to
exercise the dt-aware filter. No sim, no hardware.

    python3 scripts/lead_cross_approach.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tasks.project.packages.control import motors_from_decision
from tasks.project.packages.world_model import LaneObs, RedLineObs, WorldModel
from tasks.project_lead.packages.fsm import LeadFSM, STATE_CROSS, STATE_LANE

# dt jitter cycle (~3.8 FPS nominal) to confirm the filter is frame-time aware.
_DTS = (0.18, 0.26, 0.40, 0.26)

# Approach trace: (lane steering_suggestion, red line present?). The steer ramps
# into a right spike over the last lane frames, then the red line fires; after
# that the straight cross drives the wheels (lane suggestion no longer used).
_TRACE = [
    (0.00, False), (0.00, False), (0.05, False), (0.12, False),
    (0.26, False), (0.30, False),          # <-- the right spike, still in LANE
    (0.30, True),                          # <-- red line fires -> CROSS_STRAIGHT
    (0.00, True), (0.00, True), (0.00, False), (0.00, False), (0.00, False),
]


def _cfg(enabled):
    return dict(
        route=["straight", "stop"], cruise_speed=0.30, maneuver_timed=True,
        cross_time_s=5.0, stopline_fire_dist=0.45, stopline_fire_width=0.40,
        steer_lp_enabled=enabled, steer_lp_hz=1.0, steer_slew_per_s=0.55,
    )


def _run(enabled):
    fsm = LeadFSM(_cfg(enabled))
    t = 0.0
    rows = []
    for i, (steer, has_red) in enumerate(_TRACE):
        red = RedLineObs(present=True, area_px=1000, width_frac=0.6, dist_proxy=0.6) if has_red else None
        lane = LaneObs(steering_suggestion=steer, base_speed_suggestion=0.0,
                       lane_pixels=(0 if has_red else 600), is_curve=False,
                       healthy=(not has_red))
        wm = WorldModel(t=t, frame_w=640, frame_h=480, lane=lane,
                        leader=None, signs=[], red_line=red)
        d = fsm.step(wm, turn_yaw_rad=0.0, fwd_dist_m=0.0, odo_source="encoders")
        left, right = motors_from_decision(d)
        rows.append(dict(i=i, t=t, raw=steer, state=d.state_name,
                         steer=d.steering, left=left, right=right, diff=right - left))
        t += _DTS[i % len(_DTS)]
    return rows


def _boundary_step(rows):
    """|Δ(right-left)| across the last LANE -> first CROSS frame: the jerk."""
    lane = [r for r in rows if r["state"] == STATE_LANE]
    cross = [r for r in rows if r["state"] == STATE_CROSS]
    return abs(cross[0]["diff"] - lane[-1]["diff"]), lane[-1], cross


def main():
    off = _run(False)
    on = _run(True)

    hdr = f"{'#':>2} {'dt~':>5} {'raw':>6} {'state':<14} {'steer':>7} {'L':>6} {'R':>6} {'R-L':>7}"
    for title, rows in (("BASELINE (smoother OFF)", off), ("SMOOTHED (smoother ON)", on)):
        print(f"\n=== {title} ===")
        print(hdr)
        for r in rows:
            print(f"{r['i']:>2} {r['t']:>5.2f} {r['raw']:>6.2f} {r['state']:<14} "
                  f"{r['steer']:>+7.3f} {r['left']:>6.3f} {r['right']:>6.3f} {r['diff']:>+7.3f}")

    off_step, off_lane, off_cross = _boundary_step(off)
    on_step, on_lane, on_cross = _boundary_step(on)

    print("\n=== LANE -> CROSS boundary (the jerk) ===")
    print(f"  baseline: last-LANE diff {off_lane['diff']:+.3f} -> first-CROSS diff "
          f"{off_cross[0]['diff']:+.3f}   step |Δ| = {off_step:.3f}")
    print(f"  smoothed: last-LANE diff {on_lane['diff']:+.3f} -> first-CROSS diff "
          f"{on_cross[0]['diff']:+.3f}   step |Δ| = {on_step:.3f}")

    cross_diffs = [c["diff"] for c in on_cross[:4]]
    monotonic = all(b <= a + 1e-9 for a, b in zip(cross_diffs, cross_diffs[1:]))

    checks = [
        ("baseline reproduces the jerk (boundary step > 0.25)", off_step > 0.25),
        ("smoothed boundary step is far smaller (< baseline * 0.25)", on_step < off_step * 0.25),
        ("smoothed cross differential decays monotonically (ramp, not snap)", monotonic),
        ("baseline cross snaps straight immediately (|diff| < 0.02)", abs(off_cross[0]["diff"]) < 0.02),
    ]
    print("\n=== acceptance ===")
    ok = True
    for name, passed in checks:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        ok = ok and passed
    print(f"\n{'ALL CHECKS PASSED' if ok else 'SOME CHECKS FAILED'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
