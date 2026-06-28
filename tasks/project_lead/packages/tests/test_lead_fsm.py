"""Pure-logic tests for the lead FSM (no vision / no hardware).

Run:  python -m pytest tasks/project_lead/packages/tests/test_lead_fsm.py
  or:  python tasks/project_lead/packages/tests/test_lead_fsm.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from tasks.project_lead.packages.fsm import (  # noqa: E402
    LeadFSM, STATE_LANE, STATE_STOP, STATE_TURN_R, STATE_TURN_L, STATE_SLOW_AFTER,
    STATE_DONE, STATE_SLOW, STATE_CROSS, STATE_LOST,
)
from tasks.project.packages.control import apply_deadzone, motors_from_decision  # noqa: E402
from tasks.project.packages.world_model import LaneObs, RedLineObs, SignObs, WorldModel  # noqa: E402


def _lane(healthy=True, steer=0.0):
    return LaneObs(steering_suggestion=steer, base_speed_suggestion=0.0,
                   lane_pixels=600 if healthy else 0, is_curve=False, healthy=healthy)


def _wm(t, lane_healthy=True, signs=None, red=None, steer=0.0):
    return WorldModel(t=t, frame_w=640, frame_h=480, lane=_lane(lane_healthy, steer),
                      leader=None, signs=signs or [], red_line=red)


def _stop():
    return SignObs(kind="STOP", bbox=(0, 0, 10, 10), score=1.0)


def _red(present=True, width=0.6, dist=0.6):
    return RedLineObs(present=present, area_px=1000, width_frac=width, dist_proxy=dist)


def _cfg(**over):
    base = dict(route=["right", "stop"], cruise_speed=0.3, stop_duration=1.0,
                min_turn_s=0.8, max_turn_s=3.0, slow_after_turn_s=2.0,
                stopline_fire_dist=0.45, stopline_fire_width=0.40, stopline_clear_frames=2)
    base.update(over)
    return base


def test_default_is_lane_follow():
    fsm = LeadFSM(_cfg())
    d = fsm.step(_wm(0.0))
    assert d.state_name == STATE_LANE
    assert abs(d.base_speed - 0.3) < 1e-9


def test_slow_sign_slows():
    fsm = LeadFSM(_cfg())
    d = fsm.step(_wm(0.0, signs=[SignObs(kind="SLOW", bbox=(0, 0, 5, 5), score=1.0)]))
    assert d.state_name == STATE_SLOW
    assert d.base_speed < 0.3


def test_stop_sign_remembered_then_stop_then_turn_then_slow_after():
    fsm = LeadFSM(_cfg())

    # sign seen far from the line -> still lane following, but remembered
    assert fsm.step(_wm(0.1, signs=[_stop()])).state_name == STATE_LANE
    assert fsm._sign_pending is True

    # reach the red line (sign gone) -> STOP at sign, route step queued
    d = fsm.step(_wm(0.2, red=_red()))
    assert d.state_name == STATE_STOP and d.base_speed == 0.0
    assert fsm.route_idx == 1

    # still halting; the line is still in view -> no double advance
    d = fsm.step(_wm(0.5, red=_red(), lane_healthy=False))
    assert d.state_name == STATE_STOP
    assert fsm.route_idx == 1  # latch prevents re-fire

    # stop expires -> begin the queued right turn (lane gone mid-intersection)
    d = fsm.step(_wm(1.25, red=_red(present=False), lane_healthy=False))
    assert d.state_name == STATE_TURN_R and d.base_speed > 0.0

    # still turning before min_turn_s
    assert fsm.step(_wm(1.4, lane_healthy=False)).state_name == STATE_TURN_R

    # lane reacquired after min_turn_s -> SLOW_AFTER_TURN + lane-reset request
    d = fsm.step(_wm(2.2, lane_healthy=True))
    assert d.state_name == STATE_SLOW_AFTER
    assert fsm.request_lane_reset is True

    # within the slow-after window
    assert fsm.step(_wm(2.5, lane_healthy=True)).state_name == STATE_SLOW_AFTER

    # window over -> back to lane following
    assert fsm.step(_wm(4.3, lane_healthy=True)).state_name == STATE_LANE


def test_turn_directions_match_lane_convention():
    # Lane convention: negative steering => turn right, positive => turn left.
    right = LeadFSM(_cfg(route=["right", "stop"]))
    d = right.step(_wm(0.2, red=_red(), lane_healthy=False))   # no sign -> immediate maneuver
    assert d.state_name == STATE_TURN_R and d.steering < 0.0

    left = LeadFSM(_cfg(route=["left", "stop"]))
    d = left.step(_wm(0.2, red=_red(), lane_healthy=False))
    assert d.state_name == STATE_TURN_L and d.steering > 0.0


def test_near_stop_sign_halts_directly():
    # A STOP tag that fills enough of the frame is "close": the bot must halt on
    # the sign itself, with no red stop-line (symmetric with SLOW acting now).
    fsm = LeadFSM(_cfg(stop_tag_near_area_frac=0.04, stop_tag_halt_s=1.0))
    near = SignObs(kind="STOP", bbox=(100, 100, 300, 300), score=1.0)  # 200*200/(640*480)=0.13
    d = fsm.step(_wm(0.0, signs=[near]))
    assert d.state_name == STATE_STOP and d.base_speed == 0.0
    assert fsm.step(_wm(0.5)).state_name == STATE_STOP   # still within the halt window
    assert fsm.step(_wm(1.1)).state_name == STATE_LANE   # window over -> resume


def test_far_stop_sign_does_not_halt_directly():
    # A small / far STOP tag is only remembered for the intersection, not a direct
    # halt -- preserves the Duckietown red-line semantics.
    fsm = LeadFSM(_cfg(stop_tag_near_area_frac=0.04))
    far = SignObs(kind="STOP", bbox=(0, 0, 20, 20), score=1.0)         # 400/307200=0.0013
    d = fsm.step(_wm(0.0, signs=[far]))
    assert d.state_name == STATE_LANE
    assert fsm._sign_pending is True                     # still latched for the red line


def test_route_exhaustion_final_stop():
    fsm = LeadFSM(_cfg(route=["stop"]))
    # first intersection consumes the only step "stop" (no sign pending here)
    d = fsm.step(_wm(0.2, red=_red()))
    assert d.state_name == STATE_DONE and d.base_speed == 0.0
    # stays done
    assert fsm.step(_wm(0.4)).state_name == STATE_DONE


def test_no_fire_until_line_clears():
    fsm = LeadFSM(_cfg(route=["straight", "straight", "stop"]))
    fsm.step(_wm(0.2, red=_red()))           # fires -> straight maneuver
    idx_after_first = fsm.route_idx
    # red still present next frame: must not advance again
    fsm.step(_wm(0.25, red=_red(), lane_healthy=False))
    assert fsm.route_idx == idx_after_first


def test_one_line_does_not_burn_route_during_maneuver():
    # Regression for the route-burn latch bug: a single physical red line that
    # flickers out of view DURING the turn / slow-after window must not re-arm
    # the latch and consume extra route steps (which would reach terminal 'stop').
    fsm = LeadFSM(_cfg(route=["right", "straight", "stop"], stopline_clear_frames=2,
                       min_turn_s=0.8, slow_after_turn_s=2.0))

    # First (and only physical) intersection: fire -> right turn.
    fsm.step(_wm(0.2, red=_red(), lane_healthy=False))
    assert fsm.route_idx == 1

    # Turn completes once the lane is reacquired -> SLOW_AFTER window opens.
    assert fsm.step(_wm(1.1, lane_healthy=True)).state_name == STATE_SLOW_AFTER

    # The SAME line drops out of the band for >= clear_frames during slow-after.
    fsm.step(_wm(1.5, red=_red(present=False), lane_healthy=True))
    fsm.step(_wm(1.9, red=_red(present=False), lane_healthy=True))

    # Slow-after ends with the same line back in view: must NOT advance again.
    fsm.step(_wm(3.5, red=_red(), lane_healthy=True))
    assert fsm.route_idx == 1, "single red line re-fired and burned the route"


def test_cross_exits_on_distance():
    # Closed-loop straight cross exits when the encoder distance reaches target,
    # well before the max_cross_s safety timeout.
    fsm = LeadFSM(_cfg(route=["straight", "stop"], cross_distance_m=0.35, max_cross_s=99.0))
    d = fsm.step(_wm(0.2, red=_red(), lane_healthy=False), turn_yaw_rad=0.0, fwd_dist_m=0.0)
    assert d.state_name == STATE_CROSS
    d = fsm.step(_wm(0.4, lane_healthy=False), turn_yaw_rad=0.0, fwd_dist_m=0.20)
    assert d.state_name == STATE_CROSS                       # not yet at target
    d = fsm.step(_wm(0.6, lane_healthy=False), turn_yaw_rad=0.0, fwd_dist_m=0.35)
    assert d.state_name == STATE_SLOW_AFTER                  # distance reached -> exit


def test_cross_holds_heading_sign():
    # Heading-hold during a cross must steer AGAINST the drift (control.py sign).
    fsm = LeadFSM(_cfg(route=["straight", "stop"], cross_distance_m=1.0, max_cross_s=99.0))
    fsm.step(_wm(0.2, red=_red(), lane_healthy=False), turn_yaw_rad=0.0, fwd_dist_m=0.0)
    d = fsm.step(_wm(0.4, lane_healthy=False), turn_yaw_rad=0.5, fwd_dist_m=0.1)
    assert d.state_name == STATE_CROSS and d.steering < 0.0  # drifting left -> steer right

    fsm2 = LeadFSM(_cfg(route=["straight", "stop"], cross_distance_m=1.0, max_cross_s=99.0))
    fsm2.step(_wm(0.2, red=_red(), lane_healthy=False), turn_yaw_rad=0.0, fwd_dist_m=0.0)
    d = fsm2.step(_wm(0.4, lane_healthy=False), turn_yaw_rad=-0.5, fwd_dist_m=0.1)
    assert d.state_name == STATE_CROSS and d.steering > 0.0  # drifting right -> steer left


def test_turn_exits_on_yaw_target():
    # Right turn: yaw goes negative; exits when |yaw| reaches the heading target.
    fsm = LeadFSM(_cfg(route=["right", "stop"], turn_yaw_target_rad=1.40, max_turn_s=99.0))
    d = fsm.step(_wm(0.2, red=_red(), lane_healthy=False), turn_yaw_rad=0.0, fwd_dist_m=0.0)
    assert d.state_name == STATE_TURN_R and d.steering < 0.0
    d = fsm.step(_wm(0.5, lane_healthy=False), turn_yaw_rad=-0.7, fwd_dist_m=0.0)
    assert d.state_name == STATE_TURN_R                      # not yet at target
    d = fsm.step(_wm(0.8, lane_healthy=False), turn_yaw_rad=-1.4, fwd_dist_m=0.0)
    assert d.state_name == STATE_SLOW_AFTER                  # heading reached -> exit

    left = LeadFSM(_cfg(route=["left", "stop"], turn_yaw_target_rad=1.40, max_turn_s=99.0))
    d = left.step(_wm(0.2, red=_red(), lane_healthy=False), turn_yaw_rad=0.0, fwd_dist_m=0.0)
    assert d.state_name == STATE_TURN_L and d.steering > 0.0


def test_turn_p_control_tapers():
    # The turn steer tapers as the remaining yaw error shrinks, with a floor.
    fsm = LeadFSM(_cfg(route=["left", "stop"], turn_yaw_target_rad=1.40, max_turn_s=99.0,
                       turn_steer=0.25, turn_kp=0.8, left_widen=1.0))
    fsm.step(_wm(0.2, red=_red(), lane_healthy=False), turn_yaw_rad=0.0, fwd_dist_m=0.0)
    s_early = fsm.step(_wm(0.4, lane_healthy=False), turn_yaw_rad=0.3, fwd_dist_m=0.0).steering
    s_late  = fsm.step(_wm(0.6, lane_healthy=False), turn_yaw_rad=1.2, fwd_dist_m=0.0).steering
    assert abs(s_late) < abs(s_early)                        # tapers toward target
    assert abs(s_late) >= 0.10 - 1e-9                        # never below the floor


def test_left_turn_wider_than_right():
    # Left turns must sweep a wider arc than rights at a grid intersection, i.e.
    # steer LESS for the same yaw progress (radius ~ base / steer).
    common = dict(turn_yaw_target_rad=1.40, max_turn_s=99.0, turn_steer=0.25,
                  turn_kp=0.8, left_widen=0.7)

    right = LeadFSM(_cfg(route=["right", "stop"], **common))
    right.step(_wm(0.2, red=_red(), lane_healthy=False), turn_yaw_rad=0.0, fwd_dist_m=0.0)
    sr = right.step(_wm(0.4, lane_healthy=False), turn_yaw_rad=-0.3, fwd_dist_m=0.0).steering

    left = LeadFSM(_cfg(route=["left", "stop"], **common))
    left.step(_wm(0.2, red=_red(), lane_healthy=False), turn_yaw_rad=0.0, fwd_dist_m=0.0)
    sl = left.step(_wm(0.4, lane_healthy=False), turn_yaw_rad=0.3, fwd_dist_m=0.0).steering

    assert sr < 0.0 and sl > 0.0            # directions unchanged
    assert abs(sl) < abs(sr)                # left steers less -> wider radius


def test_left_widen_one_is_symmetric():
    # left_widen=1.0 restores mirror-symmetry: right turns are untouched and the
    # left/right steer magnitudes match at equal yaw progress.
    common = dict(turn_yaw_target_rad=1.40, max_turn_s=99.0, turn_steer=0.25,
                  turn_kp=0.8, left_widen=1.0)

    right = LeadFSM(_cfg(route=["right", "stop"], **common))
    right.step(_wm(0.2, red=_red(), lane_healthy=False), turn_yaw_rad=0.0, fwd_dist_m=0.0)
    sr = right.step(_wm(0.4, lane_healthy=False), turn_yaw_rad=-0.3, fwd_dist_m=0.0).steering

    left = LeadFSM(_cfg(route=["left", "stop"], **common))
    left.step(_wm(0.2, red=_red(), lane_healthy=False), turn_yaw_rad=0.0, fwd_dist_m=0.0)
    sl = left.step(_wm(0.4, lane_healthy=False), turn_yaw_rad=0.3, fwd_dist_m=0.0).steering

    assert abs(abs(sl) - abs(sr)) < 1e-9


def test_no_odometry_falls_back_to_timed():
    # With no encoder scalars, behaviour is the legacy timed / lane-reacquire path.
    fsm = LeadFSM(_cfg(route=["straight", "stop"], min_cross_s=0.4, max_cross_s=3.0))
    assert fsm.step(_wm(0.2, red=_red(), lane_healthy=False)).state_name == STATE_CROSS
    assert fsm.step(_wm(0.5, lane_healthy=True)).state_name == STATE_CROSS      # before min_cross_s
    assert fsm.step(_wm(0.7, lane_healthy=True)).state_name == STATE_SLOW_AFTER  # lane reacquired


def test_max_timeout_overrides_odometry():
    # Even with no forward progress, the cross exits at the max_cross_s safety net.
    fsm = LeadFSM(_cfg(route=["straight", "stop"], cross_distance_m=99.0, max_cross_s=1.0))
    fsm.step(_wm(0.2, red=_red(), lane_healthy=False), turn_yaw_rad=0.0, fwd_dist_m=0.0)
    assert fsm.step(_wm(0.5, lane_healthy=False),
                    turn_yaw_rad=0.0, fwd_dist_m=0.0).state_name == STATE_CROSS
    assert fsm.step(_wm(1.3, lane_healthy=False),
                    turn_yaw_rad=0.0, fwd_dist_m=0.0).state_name == STATE_SLOW_AFTER


def test_real_turn_is_timed_not_odometry():
    # Real bot (maneuver_timed): a left turn runs for the full turn_time_s and
    # ignores BOTH the early lane-reacquire and the noisy encoder-yaw exits that
    # were bailing the turn out after a brief moment.
    fsm = LeadFSM(_cfg(route=["left", "stop"], maneuver_timed=True,
                       turn_time_s=3.0, cross_time_s=1.5, min_turn_s=0.8))
    d = fsm.step(_wm(0.2, red=_red(), lane_healthy=False),
                 turn_yaw_rad=0.0, fwd_dist_m=0.0, odo_source="encoders")
    assert d.state_name == STATE_TURN_L and d.steering > 0.0
    # lane "reacquires" early AND the (bad) yaw says past target -> still turning
    d = fsm.step(_wm(1.0, lane_healthy=True),
                 turn_yaw_rad=2.0, fwd_dist_m=0.0, odo_source="encoders")
    assert d.state_name == STATE_TURN_L, "timed turn bailed early on lane/odo"
    d = fsm.step(_wm(2.9, lane_healthy=True),
                 turn_yaw_rad=2.0, fwd_dist_m=0.0, odo_source="encoders")
    assert d.state_name == STATE_TURN_L
    # past turn_time_s (elapsed from the 0.2s fire) -> hand back to slow-after
    d = fsm.step(_wm(3.3, lane_healthy=True),
                 turn_yaw_rad=2.0, fwd_dist_m=0.0, odo_source="encoders")
    assert d.state_name == STATE_SLOW_AFTER


def test_real_cross_is_timed_and_straight():
    # Real bot (maneuver_timed): a straight cross runs for cross_time_s, drives
    # dead-straight (no encoder-yaw heading hold), and ignores the distance exit.
    fsm = LeadFSM(_cfg(route=["straight", "stop"], maneuver_timed=True,
                       turn_time_s=3.0, cross_time_s=1.5))
    d = fsm.step(_wm(0.2, red=_red(), lane_healthy=False),
                 turn_yaw_rad=0.5, fwd_dist_m=99.0, odo_source="encoders")
    assert d.state_name == STATE_CROSS and abs(d.steering) < 1e-9   # straight, yaw ignored
    d = fsm.step(_wm(1.0, lane_healthy=True),
                 turn_yaw_rad=0.5, fwd_dist_m=99.0, odo_source="encoders")
    assert d.state_name == STATE_CROSS, "timed cross bailed early on distance"
    d = fsm.step(_wm(1.9, lane_healthy=True),
                 turn_yaw_rad=0.5, fwd_dist_m=99.0, odo_source="encoders")
    assert d.state_name == STATE_SLOW_AFTER


def test_sim_pose_ignores_maneuver_timed():
    # The flag only governs the real-bot path: even with maneuver_timed set and a
    # huge turn_time_s, the sim (odo_source="pose") still ends the turn on its
    # exact pose-odometry yaw target rather than waiting out the timer.
    fsm = LeadFSM(_cfg(route=["left", "stop"], maneuver_timed=True, turn_time_s=99.0,
                       turn_yaw_target_rad=1.40, max_turn_s=99.0))
    fsm.step(_wm(0.2, red=_red(), lane_healthy=False),
             turn_yaw_rad=0.0, fwd_dist_m=0.0, odo_source="pose")
    d = fsm.step(_wm(0.8, lane_healthy=False),
                 turn_yaw_rad=1.4, fwd_dist_m=0.5, odo_source="pose")
    assert d.state_name == STATE_SLOW_AFTER  # yaw target reached, not held for 99s


def test_lane_lost_halts_after_grace_and_recovers():
    fsm = LeadFSM(_cfg())
    assert fsm.step(_wm(0.1)).state_name == STATE_LANE
    # unhealthy while cruising: keep driving through the grace window...
    assert fsm.step(_wm(0.5, lane_healthy=False)).state_name == STATE_LANE
    assert fsm.step(_wm(2.0, lane_healthy=False)).state_name == STATE_LANE
    # ...then halt instead of cruising blind
    d = fsm.step(_wm(2.3, lane_healthy=False))
    assert d.state_name == STATE_LOST and d.base_speed == 0.0
    # markings back in view -> resume
    assert fsm.step(_wm(2.4)).state_name == STATE_LANE


def test_lane_lost_grace_restarts_after_maneuver():
    # Regression (real bot): the unhealthy clock ran on through the maneuver +
    # slow-after window (where no markings is normal), so the bot halted on the
    # FIRST cruising frame after every intersection instead of reacquiring.
    fsm = LeadFSM(_cfg())
    assert fsm.step(_wm(0.1)).state_name == STATE_LANE
    assert fsm.step(_wm(0.2, red=_red(), lane_healthy=False)).state_name == STATE_TURN_R
    # lane stays unhealthy through the whole turn (timeout exit at max_turn_s)
    assert fsm.step(_wm(1.0, lane_healthy=False)).state_name == STATE_TURN_R
    assert fsm.step(_wm(3.3, lane_healthy=False)).state_name == STATE_SLOW_AFTER
    assert fsm.step(_wm(5.25, lane_healthy=False)).state_name == STATE_SLOW_AFTER
    # window over, still unhealthy: must CRUISE (full grace), not halt
    assert fsm.step(_wm(5.4, lane_healthy=False)).state_name == STATE_LANE
    assert fsm.step(_wm(7.0, lane_healthy=False)).state_name == STATE_LANE
    # grace spent without reacquiring -> now halt
    d = fsm.step(_wm(7.5, lane_healthy=False))
    assert d.state_name == STATE_LOST and d.base_speed == 0.0


# --- steering smoother (real-only): kills the pre-intersection right jerk ------
# All of these must set maneuver_timed=True so the smoother engages (it is gated
# to the real timed path). Default knobs: steer_lp_hz=1.0, steer_slew_per_s=0.55.
DT = 0.26  # ~3.8 FPS, the real bot's observed frame time

def _smcfg(**over):
    base = dict(route=["straight", "stop"], cruise_speed=0.3, maneuver_timed=True,
                cross_time_s=5.0, turn_steer=0.25, left_widen=0.7)
    base.update(over)
    return _cfg(**base)


def test_steer_input_spike_is_slew_capped_and_reaches_base_speed():
    # The load-bearing claim: smoothing the INPUT bounds the spike AND feeds the
    # speed scale, so base_speed is no longer collapsed by the RAW steer.
    fsm = LeadFSM(_smcfg())
    t = 0.0
    for _ in range(4):                                   # settle straight
        fsm.step(_wm(t, steer=0.0), odo_source="encoders"); t += DT
    d = fsm.step(_wm(t, steer=0.30), odo_source="encoders")   # the right spike
    assert d.state_name == STATE_LANE
    assert abs(d.steering) <= 0.55 * DT + 1e-6           # capped to the slew ceiling
    assert abs(d.steering) < 0.30                        # strictly below the raw spike
    # base_speed derives from the SMOOTHED steer (~0.14 -> ~0.19), not the raw
    # 0.30 (which would floor base to 0.3*0.5 = 0.15). Proves input-smoothing.
    assert d.base_speed > 0.16


def test_lane_to_cross_ramps_down_not_snaps():
    # Sustained right steer through the approach, then the intersection fires the
    # straight cross. Steering must DECAY through the cross, not snap to 0.
    fsm = LeadFSM(_smcfg())
    t = 0.0
    for _ in range(6):                                   # settle the smoother near 0.30
        fsm.step(_wm(t, steer=0.30), odo_source="encoders"); t += DT
    pre = fsm._steer_cmd
    assert pre > 0.25                                    # smoother tracked the steer
    d = fsm.step(_wm(t, red=_red(), lane_healthy=False), turn_yaw_rad=0.0,
                 fwd_dist_m=0.0, odo_source="encoders"); t += DT
    assert d.state_name == STATE_CROSS
    assert 0.0 < d.steering < pre                        # decaying, still turning (no snap)
    prev = d.steering
    for _ in range(4):                                   # strictly decreasing toward straight
        d = fsm.step(_wm(t, lane_healthy=False), turn_yaw_rad=0.0,
                     fwd_dist_m=0.0, odo_source="encoders"); t += DT
        assert d.state_name == STATE_CROSS and d.steering < prev + 1e-9
        prev = d.steering
    assert abs(prev) < 0.02                              # essentially straight within ~4 frames


def test_smoothed_wheel_jerk_smaller_than_unsmoothed():
    # WHEEL-level assertion (per the spec): the single-frame change in the wheel
    # differential at the LANE->CROSS boundary must be far smaller when smoothed.
    def run(enabled):
        fsm = LeadFSM(_smcfg(steer_lp_enabled=enabled))
        t, diffs = 0.0, []
        seq = [(0.30, None)] * 6 + [(0.0, _red())]       # approach, then fire cross
        for steer, red in seq:
            d = fsm.step(_wm(t, steer=steer, red=red, lane_healthy=(red is None)),
                         turn_yaw_rad=0.0, fwd_dist_m=0.0, odo_source="encoders")
            left, right = motors_from_decision(d)
            diffs.append((d.state_name, right - left))
            t += DT
        # boundary = last LANE diff vs first CROSS diff
        lane_diffs = [v for s, v in diffs if s == STATE_LANE]
        cross_diffs = [v for s, v in diffs if s == STATE_CROSS]
        return abs(cross_diffs[0] - lane_diffs[-1])

    smoothed = run(True)
    unsmoothed = run(False)
    assert unsmoothed > 0.25                             # the real jerk: ~0.30 -> 0 snap
    assert smoothed < unsmoothed * 0.25                  # smoothed boundary step is tiny


def test_timed_turn_arc_is_not_smoothed():
    # Constraint 2: the timed turn arc must pass through EXACTLY (no lag), so its
    # field-tuned swept angle over turn_time_s is preserved.
    fsm = LeadFSM(_smcfg(route=["left", "stop"], turn_time_s=5.0))
    t = 0.0
    expect = 0.25 * 0.7                                  # turn_steer * left_widen
    d = fsm.step(_wm(t, red=_red(), lane_healthy=False), turn_yaw_rad=0.0,
                 fwd_dist_m=0.0, odo_source="encoders"); t += DT
    assert d.state_name == STATE_TURN_L
    for _ in range(4):                                   # exact on the first AND every frame
        assert abs(d.steering - expect) < 1e-9
        d = fsm.step(_wm(t, lane_healthy=False), turn_yaw_rad=0.0,
                     fwd_dist_m=0.0, odo_source="encoders"); t += DT


def test_sim_lane_bypasses_smoother():
    # The sim is gated out by the PRESENCE of a Godot pose, not by odo_source:
    # in lane states odo_source is None on BOTH platforms (it is only "pose"
    # inside maneuvers). With a pose supplied (sim), the smoother must be OFF and
    # emit the raw lane steer, leaving the tuned sim closed loop untouched.
    fsm = LeadFSM(_smcfg())
    t = 0.0
    last = None
    for _ in range(4):
        last = fsm.step(_wm(t, steer=0.30), pose=(0.0, 0.0, 0.0), odo_source=None); t += DT
    assert last.state_name == STATE_LANE
    assert abs(last.steering - 0.30) < 1e-9              # exact passthrough in the sim lane state


def test_frame_dropout_does_not_reopen_the_jerk():
    # A long gap (dropped camera frames) must NOT let the slew cap go non-binding:
    # dt is capped at steer_dt_max, so the recovery-frame step stays bounded
    # instead of jumping the full multi-second worth of slew.
    fsm = LeadFSM(_smcfg(steer_dt_max=0.4, steer_slew_per_s=0.55))
    fsm.step(_wm(0.0, steer=0.0), odo_source="encoders")          # seed at 0
    d = fsm.step(_wm(5.0, steer=0.40), odo_source="encoders")     # 5 s gap, then a spike
    assert d.state_name == STATE_LANE
    assert abs(d.steering) <= 0.55 * 0.4 + 1e-6                   # bounded by slew * dt_max


def test_negative_slew_rate_is_safe():
    # A misconfigured negative slew rate must not invert the clamp; it is floored
    # to 0 at load and degrades to low-pass-only (still bounded, correct sign).
    fsm = LeadFSM(_smcfg(steer_slew_per_s=-0.5))
    assert fsm.steer_slew_per_s == 0.0
    t = 0.0
    fsm.step(_wm(t, steer=0.0), odo_source="encoders"); t += DT
    d = fsm.step(_wm(t, steer=0.30), odo_source="encoders")
    assert 0.0 < d.steering < 0.30                                # low-pass only, no blow-up


def test_disabled_knob_is_identity():
    # steer_lp_enabled=False reproduces today's exact steering (regression A/B).
    fsm = LeadFSM(_smcfg(steer_lp_enabled=False))
    t = 0.0
    for _ in range(3):
        fsm.step(_wm(t, steer=0.0), odo_source="encoders"); t += DT
    d = fsm.step(_wm(t, steer=0.30), odo_source="encoders")
    assert abs(d.steering - 0.30) < 1e-9


def test_first_frame_no_transient():
    # First-ever step with dt=0 (no prior timestamp): hold the 0.0 seed, no jump.
    fsm = LeadFSM(_smcfg())
    d = fsm.step(_wm(0.0, steer=0.20), odo_source="encoders")
    assert d.state_name == STATE_LANE
    assert abs(d.steering) < 1e-9


def test_deadzone_lift_preserves_turn_on_a_curve():
    # Inner wheel below breakaway: lift BOTH by the deficit so the inner wheel
    # moves and the steering differential survives (does NOT flatten to a near-
    # straight, which would run the bot off the curve).
    left, right = apply_deadzone(0.08, 0.28, 0.22)
    assert abs(left - 0.22) < 1e-9 and abs(right - 0.42) < 1e-9
    assert abs((right - left) - 0.20) < 1e-9   # original differential preserved


def test_deadzone_leaves_full_stop_at_zero():
    # A commanded halt must stay halted — never creep.
    assert apply_deadzone(0.0, 0.0, 0.22) == (0.0, 0.0)


def test_deadzone_both_above_breakaway_untouched():
    # Both wheels already clear the breakaway: no change.
    assert apply_deadzone(0.30, 0.50, 0.22) == (0.30, 0.50)


def test_deadzone_straight_creep_both_lifted():
    # Near-straight slow creep: both below breakaway, both lifted, tiny
    # differential kept.
    left, right = apply_deadzone(0.15, 0.18, 0.22)
    assert abs(left - 0.22) < 1e-9 and abs(right - 0.25) < 1e-9


def test_deadzone_zero_is_identity():
    # 0 (and the default / sim path) disables the lift entirely.
    assert apply_deadzone(0.1, 0.0, 0.0) == (0.1, 0.0)


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok: {fn.__name__}")
    print(f"PASSED {len(fns)} lead-FSM tests")


if __name__ == "__main__":
    _run()
