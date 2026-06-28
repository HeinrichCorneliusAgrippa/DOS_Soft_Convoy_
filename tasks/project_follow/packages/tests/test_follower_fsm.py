"""Pure-logic tests for the follower FSM (no vision / no hardware).

Run:  python -m pytest tasks/project_follow/packages/tests/test_follower_fsm.py
  or:  python tasks/project_follow/packages/tests/test_follower_fsm.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

from tasks.project_follow.packages.fsm import (  # noqa: E402
    FollowerFSM, STATE_WAIT, STATE_FOLLOW, STATE_CLOSE_STOP, STATE_REACQUIRE,
    STATE_TURN, STATE_LANE, STATE_HOLD,
)
from tasks.project.packages.fsm_common import lateral_to_steer  # noqa: E402
from tasks.project.packages.world_model import LaneObs, LeaderObs, SignObs, WorldModel  # noqa: E402


def _lane(healthy=True, steer=0.0):
    return LaneObs(steering_suggestion=steer, base_speed_suggestion=0.0,
                   lane_pixels=600 if healthy else 0, is_curve=False, healthy=healthy)


def _leader(span, lateral=0.0, score=1.0, heading=None):
    return LeaderObs(bbox=(0, 0, 10, 10), distance_px=span, lateral=lateral,
                     score=score, pair_px=span, source="grid", heading=heading)


def _wm(t, leader=None, lane_healthy=True, steer=0.0, signs=None):
    return WorldModel(t=t, frame_w=640, frame_h=480, lane=_lane(lane_healthy, steer),
                      leader=leader, signs=signs or [], red_line=None)


def _cfg(**over):
    base = dict(cruise_speed=0.3, grid_safe_px=18, grid_stop_px=70, grid_close_px=55,
                grid_arm_px=60, leader_p_gain=0.6, max_steer=0.4, leader_lost_grace_s=1.0,
                reacquire_creep_speed=0.12, reacquire_steer_gain=0.8)
    base.update(over)
    return base


def test_startup_waits_until_gap_opens():
    fsm = FollowerFSM(_cfg())
    # lead present but too close (no gap) -> WAIT, not armed
    d = fsm.step(_wm(0.0, leader=_leader(span=80)))
    assert d.state_name == STATE_WAIT and d.base_speed == 0.0
    assert fsm._armed is False
    # gap opens (span <= arm) -> armed and following
    d = fsm.step(_wm(0.1, leader=_leader(span=50)))
    assert d.state_name == STATE_FOLLOW and fsm._armed is True


def test_follow_speed_tapers_with_distance():
    fsm = FollowerFSM(_cfg())
    fsm.step(_wm(0.0, leader=_leader(span=50)))           # arm
    far = fsm.step(_wm(0.1, leader=_leader(span=18)))     # safe -> full speed
    near = fsm.step(_wm(0.2, leader=_leader(span=70)))    # stop band -> zero
    assert abs(far.base_speed - 0.3) < 1e-6
    assert near.base_speed == 0.0                          # implicit stop when lead halts


def test_follow_steers_toward_leader():
    fsm = FollowerFSM(_cfg())
    fsm.step(_wm(0.0, leader=_leader(span=40)))
    d = fsm.step(_wm(0.1, leader=_leader(span=40, lateral=0.5)))
    assert abs(d.steering - lateral_to_steer(0.5, 0.6, 0.4)) < 1e-9


def test_lost_far_centered_reacquires():
    fsm = FollowerFSM(_cfg())
    fsm.step(_wm(0.0, leader=_leader(span=20, lateral=0.1)))   # arm, far, near-centered
    d = fsm.step(_wm(0.5, leader=None, lane_healthy=False))    # within grace, no turn cue
    assert d.state_name == STATE_REACQUIRE
    assert abs(d.base_speed - 0.12) < 1e-9
    assert abs(d.steering - lateral_to_steer(0.1, 0.8, 0.4)) < 1e-9


def test_lost_swept_sideways_mimics_the_leaders_turn():
    # Marker swept right out of frame => leader turned right: approach the
    # vanish point in-lane (no preemptive steering), then arc right, then
    # resume pursuit/follow.
    fsm = FollowerFSM(_cfg(span_to_dist_k=17.5, turn_lateral_min=0.18,
                           turn_approach_margin_m=0.05,
                           pursuit_turn_speed=0.15, pursuit_turn_steer=0.28,
                           turn_yaw_target_rad=1.35, post_turn_settle_s=3.0))
    fsm.step(_wm(0.0, leader=_leader(span=35, lateral=0.5)))   # arm, leader sweeping right
    # Loss within grace: approach phase holds the entry heading (NOT lane
    # steering — the lane follower would bend into the corner early).
    d = fsm.step(_wm(0.2, leader=None, lane_healthy=True, steer=0.05),
                 turn_yaw_rad=0.0, fwd_dist_m=0.0)
    assert d.state_name == STATE_TURN
    assert abs(d.steering) < 1e-9                  # straight to the corner
    # Approach distance covered (gap = 17.5/35 = 0.5 m) -> turn phase, right arc.
    d = fsm.step(_wm(0.5, leader=None, lane_healthy=True),
                 turn_yaw_rad=0.0, fwd_dist_m=0.5)
    assert d.state_name == STATE_TURN
    assert d.steering < 0                          # negative steer = right
    # Yaw target reached -> maneuver done.
    d = fsm.step(_wm(1.0, leader=None, lane_healthy=True),
                 turn_yaw_rad=-1.4, fwd_dist_m=0.6)
    assert d.state_name == STATE_TURN              # completion frame
    # Post-turn settle: hold still so the tailgated leader pulls away into
    # detection range (the grid must fit in the frame to be found).
    d = fsm.step(_wm(1.5, leader=None, lane_healthy=True))
    assert d.state_name == STATE_CLOSE_STOP and d.base_speed == 0.0
    # Settle expired without a sighting -> lane pursuit at pursuit pace.
    d = fsm.step(_wm(4.5, leader=None, lane_healthy=True))
    assert d.state_name == STATE_LANE
    assert abs(d.base_speed - 0.15) < 1e-9         # pursuit pace, not cruise
    # Marker reappears at any point -> straight back to FOLLOW.
    d = fsm.step(_wm(5.0, leader=_leader(span=40)))
    assert d.state_name == STATE_FOLLOW


def test_lost_close_stops_not_coasts():
    fsm = FollowerFSM(_cfg())
    fsm.step(_wm(0.0, leader=_leader(span=60)))                # arm, close (>= grid_close_px)
    d = fsm.step(_wm(0.3, leader=None, lane_healthy=False))    # within grace but close
    assert d.state_name == STATE_CLOSE_STOP and d.base_speed == 0.0


def test_lost_past_grace_falls_back_to_lane_then_hold():
    fsm = FollowerFSM(_cfg(pursuit_timeout_s=10.0, pursuit_speed=0.15))
    fsm.step(_wm(0.0, leader=_leader(span=20)))                # arm
    # Within the pursuit window: lane-follow at pursuit pace, NOT cruise —
    # cruising blindly overtakes the leader inside its corner.
    lane = fsm.step(_wm(2.0, leader=None, lane_healthy=True))
    assert lane.state_name == STATE_LANE and abs(lane.base_speed - 0.15) < 1e-9
    # Unhealthy lane inside the pursuit window: keep creeping, don't park
    # (corners flicker the lane-health threshold).
    creep = fsm.step(_wm(2.1, leader=None, lane_healthy=False))
    assert creep.state_name == STATE_REACQUIRE
    assert abs(creep.base_speed - 0.12) < 1e-9
    # Pursuit window expired: full cruise on a healthy lane, HOLD otherwise.
    cruise = fsm.step(_wm(11.0, leader=None, lane_healthy=True))
    assert cruise.state_name == STATE_LANE and abs(cruise.base_speed - 0.3) < 1e-9
    hold = fsm.step(_wm(11.1, leader=None, lane_healthy=False))
    assert hold.state_name == STATE_HOLD and hold.base_speed == 0.0


def test_stop_signs_are_ignored():
    fsm = FollowerFSM(_cfg())
    fsm.step(_wm(0.0, leader=_leader(span=40)))
    d = fsm.step(_wm(0.1, leader=_leader(span=40),
                     signs=[SignObs(kind="STOP", bbox=(0, 0, 5, 5), score=1.0)]))
    assert d.state_name == STATE_FOLLOW  # no STOP/SLOW behaviour on the follower


def _run():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  ok: {fn.__name__}")
    print(f"PASSED {len(fns)} follower-FSM tests")


if __name__ == "__main__":
    _run()
