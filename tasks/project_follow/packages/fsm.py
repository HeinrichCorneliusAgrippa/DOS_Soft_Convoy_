"""Follower-bot finite state machine: pure pursuit of the lead's circle-grid
back marker, with a lane-following fallback. No traffic-sign logic at all -- the
lead owns that. The follower stops *implicitly* when the lead halts (the grid
grows -> distance taper -> speed 0).

States:
  WAIT_LEAD  - startup: hold until the lead has opened a gap (don't lurch into a
               stationary lead at boot).
  FOLLOW     - marker present: distance-tapered speed, steer toward the marker.
  CLOSE_STOP - marker lost while close: STOP (don't coast into the lead).
  REACQUIRE  - marker lost within grace: slow creep + steer toward the last-seen
               bearing, to turn *into* the corner the lead took.
  PURSUIT_TURN - the marker swept sideways out of view: the lead turned a
               corner. Don't cut toward where it WAS (that overtakes it
               blindly inside the corner) — drive straight to where it
               vanished, execute our own ~90 deg turn in the observed
               direction, then resume following.
  LANE_FOLLOW- marker lost past grace, lane healthy: cruise on the lane.
  HOLD       - nothing visible for longer than the pursuit window: stop.
               (While the window is open, an unhealthy lane creeps on as
               REACQUIRE instead of parking — corners flicker lane health.)

Pure logic over a WorldModel -> unit-testable without vision/hardware.
"""
from collections import deque
from typing import Optional

from tasks.project.packages.fsm_common import (
    BLUE, OFF, RED, YELLOW, Decision, all_leds, clamp, follow_speed, lateral_to_steer,
)
from tasks.project.packages.world_model import WorldModel

STATE_WAIT       = "WAIT_LEAD"
STATE_FOLLOW     = "FOLLOW"
STATE_CLOSE_STOP = "CLOSE_STOP"
STATE_REACQUIRE  = "REACQUIRE"
STATE_TURN       = "PURSUIT_TURN"
STATE_LANE       = "LANE_FOLLOW"
STATE_HOLD       = "HOLD"


class FollowerFSM:
    def __init__(self, cfg: Optional[dict] = None):
        cfg = cfg or {}
        self.cruise_speed = float(cfg.get("cruise_speed", 0.3))
        # Distance proxy = circle-grid mean dot spacing (px). Grows as lead nears.
        self.grid_safe_px = float(cfg.get("grid_safe_px", 18))   # <= this -> full speed (far)
        self.grid_stop_px = float(cfg.get("grid_stop_px", 70))   # >= this -> zero speed (close)
        self.grid_close_px = float(cfg.get("grid_close_px", 55))  # lost-while-this-close -> STOP, don't coast
        self.grid_arm_px = float(cfg.get("grid_arm_px", self.grid_stop_px))  # arm once a gap this big exists
        self.grid_min_score = float(cfg.get("grid_min_score", 0.5))

        self.leader_p_gain = float(cfg.get("leader_p_gain", 0.6))
        self.max_steer = float(cfg.get("max_steer", 0.4))
        self.leader_lost_grace_s = float(cfg.get("leader_lost_grace_s", 1.0))

        self.reacquire_creep = float(cfg.get("reacquire_creep_speed", 0.12))
        self.reacquire_steer_gain = float(cfg.get("reacquire_steer_gain", 0.8))
        self.heading_gain = float(cfg.get("reacquire_heading_gain", 0.0))  # best-effort; off by default
        # After the grace, keep pursuing along the lane (it IS the leader's
        # path) for this long since the last sighting before parking in HOLD.
        # Tight curves flicker below the lane-health pixel threshold; without
        # this window one bad frame strands the bot mid-corner.
        self.pursuit_timeout_s = float(cfg.get("pursuit_timeout_s", 10.0))
        # Speed while pursuing a recently-seen leader. Must stay close to the
        # leader's corner pace: at full cruise the follower cuts the corner
        # and blindly OVERTAKES the leader mid-turn (a back marker can't be
        # seen from alongside). Past the window the leader is far -> cruise.
        self.pursuit_speed = float(cfg.get("pursuit_speed", 0.15))

        # --- pursuit turn (mimic the leader's corner) ---
        # A loss with the marker swept this far off-center means the leader
        # turned; below it the loss is treated as straight-ahead (washout).
        self.turn_lateral_min = float(cfg.get("turn_lateral_min", 0.18))
        # FOLLOW steers to keep the marker centered, so a turning leader
        # rarely drifts laterally before the grid breaks from obliqueness.
        # The perspective heading cue catches it instead: |heading| above
        # this within the last sightings implies a turn (sim-calibrated:
        # NEGATIVE heading = leader yawing right).
        self.turn_heading_min = float(cfg.get("turn_heading_min", 0.06))
        # Metric gap from the span proxy: d ~ span_to_dist_k / span_px
        # (k = focal_px * dot_spacing_m; FIELD-TUNE for the real board).
        self.span_to_dist_k = float(cfg.get("span_to_dist_k", 17.5))
        self.turn_approach_margin_m = float(cfg.get("turn_approach_margin_m", 0.05))
        self.pursuit_turn_speed = float(cfg.get("pursuit_turn_speed", 0.15))
        self.pursuit_turn_steer = float(cfg.get("pursuit_turn_steer", 0.28))
        self.turn_yaw_target = float(cfg.get("turn_yaw_target_rad", 1.35))
        self.max_pursuit_turn_s = float(cfg.get("max_pursuit_turn_s", 4.0))
        # After completing the corner we are typically right on the leader's
        # tail — too close for the whole grid to fit in frame. Hold still up
        # to this long so it pulls away into detection range (the same idea
        # as WAIT_LEAD's arming gap).
        self.post_turn_settle_s = float(cfg.get("post_turn_settle_s", 3.0))

        self.rear_led_indices = list(cfg.get("rear_led_indices", [3, 4]))

        self._last_leader_t = -1e9
        self._last_lateral = 0.0
        self._last_span: Optional[float] = None
        self._last_heading: Optional[float] = None
        self._head_hist = deque(maxlen=24)   # (t, heading) of recent sightings
        self._armed = False
        # Active pursuit-turn maneuver: None, or
        # {'dir': 'left'|'right', 'phase': 'approach'|'turn', 't0': float,
        #  'approach_m': float}
        self._pturn: Optional[dict] = None
        self._settle_until = -1.0

    def step(self, wm: WorldModel, turn_yaw_rad: Optional[float] = None,
             fwd_dist_m: Optional[float] = None) -> Decision:
        t = wm.t
        leader = wm.leader
        confident = leader is not None and leader.score >= self.grid_min_score

        if confident:
            self._pturn = None  # leader back in sight -> just follow it
            self._last_leader_t = t
            self._last_lateral = leader.lateral
            self._last_span = leader.pair_px if leader.pair_px is not None else leader.distance_px
            self._last_heading = leader.heading
            if leader.heading is not None:
                self._head_hist.append((t, leader.heading))
            if self._last_span is not None and self._last_span <= self.grid_arm_px:
                self._armed = True  # a real gap has opened -> safe to follow

        # Startup: don't drive until the lead has opened a gap at least once.
        if not self._armed:
            return self._mk(STATE_WAIT, 0.0, 0.0, all_leds(RED if confident else OFF))

        if confident:
            span = self._last_span if self._last_span is not None else self.grid_safe_px
            speed = follow_speed(span, self.grid_safe_px, self.grid_stop_px, self.cruise_speed)
            steer = lateral_to_steer(leader.lateral, self.leader_p_gain, self.max_steer)
            return self._mk(STATE_FOLLOW, speed, steer, self._rear_signal())

        # Marker lost. An active pursuit turn runs to completion (unless the
        # marker reappears, handled above).
        if self._pturn is not None:
            return self._run_pursuit_turn(wm, t, turn_yaw_rad, fwd_dist_m)

        # Just finished mimicking the corner: hold still while the leader
        # pulls away into detection range (tailgating keeps the grid larger
        # than the frame forever).
        if t < self._settle_until:
            return self._mk(STATE_CLOSE_STOP, 0.0, 0.0, all_leds(RED))

        within_grace = (t - self._last_leader_t) < self.leader_lost_grace_s
        if within_grace:
            # Stop-floor: lost while close (grid drops out at close range) -> STOP, not coast.
            if self._last_span is not None and self._last_span >= self.grid_close_px:
                return self._mk(STATE_CLOSE_STOP, 0.0, 0.0, all_leds(RED))
            # Did the leader turn a corner? Mimic it: drive to where it
            # vanished, then turn the same way.
            turn_dir = self._infer_turn_dir(t)
            if turn_dir is not None:
                gap_m = self.span_to_dist_k / max(self._last_span or 1.0, 1.0)
                self._pturn = {
                    'dir': turn_dir,
                    'phase': 'approach',
                    't0': t,
                    'approach_m': max(0.0, gap_m - self.turn_approach_margin_m),
                }
                return self._run_pursuit_turn(wm, t, turn_yaw_rad, fwd_dist_m)
            # Lost dead-ahead (washout): creep toward the last bearing.
            steer = lateral_to_steer(self._last_lateral, self.reacquire_steer_gain, self.max_steer)
            if self.heading_gain and self._last_heading is not None:
                steer = clamp(steer + self.heading_gain * self._last_heading,
                              -self.max_steer, self.max_steer)
            return self._mk(STATE_REACQUIRE, self.reacquire_creep, steer, all_leds(YELLOW))

        # Grace expired: pursue along the lane. While the leader was seen
        # recently it is just around the corner — match its pace instead of
        # cruising, or the follower blindly overtakes it mid-turn.
        in_pursuit = (t - self._last_leader_t) < self.pursuit_timeout_s
        if wm.lane.healthy:
            speed = self.pursuit_speed if in_pursuit else self.cruise_speed
            return self._mk(STATE_LANE, speed, wm.lane.steering_suggestion, all_leds(BLUE))
        # Lane momentarily unhealthy (tight curve / washed-out frame): keep
        # creeping inside the pursuit window instead of parking.
        if in_pursuit:
            return self._mk(STATE_REACQUIRE, self.reacquire_creep,
                            wm.lane.steering_suggestion, all_leds(YELLOW))
        return self._mk(STATE_HOLD, 0.0, 0.0, all_leds(OFF))

    def _infer_turn_dir(self, t: float) -> Optional[str]:
        """Which way did the leader go when the marker dropped? Lateral sweep
        is the geometric cue, but FOLLOW keeps the marker centered, so the
        perspective heading cue (board foreshortening) usually fires first."""
        if abs(self._last_lateral) >= self.turn_lateral_min:
            return 'right' if self._last_lateral > 0 else 'left'
        recent = [h for (ht, h) in self._head_hist if t - ht <= 0.8]
        if recent:
            best = max(recent, key=abs)
            if abs(best) >= self.turn_heading_min:
                return 'right' if best < 0 else 'left'
        return None

    def _run_pursuit_turn(self, wm: WorldModel, t: float,
                          turn_yaw_rad: Optional[float],
                          fwd_dist_m: Optional[float]) -> Decision:
        """Approach the leader's vanish point in-lane, then arc ~90 deg the
        way it went. Odometry (yaw/forward distance since maneuver entry)
        closes the loop when available; wall-clock timing is the fallback."""
        p = self._pturn
        if p['phase'] == 'approach':
            budget_s = p['approach_m'] / max(self.pursuit_turn_speed, 1e-3) + 0.5
            reached = fwd_dist_m is not None and fwd_dist_m >= p['approach_m']
            if reached or (t - p['t0']) >= budget_s:
                p['phase'] = 'turn'
                p['t0'] = t
            else:
                # Hold the entry heading to the corner. NOT lane steering: the
                # lane follower sees the curve markings and starts bending
                # early — the exact preemptive corner-cut this maneuver exists
                # to avoid.
                steer = 0.0
                if turn_yaw_rad is not None:
                    steer = clamp(-0.6 * turn_yaw_rad, -0.2, 0.2)
                return self._mk(STATE_TURN, self.pursuit_turn_speed,
                                steer, all_leds(YELLOW))
        # Turn phase. +steer turns LEFT.
        sign = 1.0 if p['dir'] == 'left' else -1.0
        turned = turn_yaw_rad is not None and abs(turn_yaw_rad) >= self.turn_yaw_target
        if turned or (t - p['t0']) >= self.max_pursuit_turn_s:
            self._pturn = None
            # Restart the pursuit clock: the corner consumed the old window.
            self._last_leader_t = t - self.leader_lost_grace_s
            self._settle_until = t + self.post_turn_settle_s
            return self._mk(STATE_TURN, self.pursuit_turn_speed,
                            wm.lane.steering_suggestion, all_leds(YELLOW))
        return self._mk(STATE_TURN, self.pursuit_turn_speed,
                        sign * self.pursuit_turn_steer, all_leds(YELLOW))

    def _rear_signal(self):
        leds = {0: OFF, 1: OFF, 2: OFF, 3: OFF, 4: OFF}
        for idx in self.rear_led_indices:
            leds[int(idx)] = RED  # signal to any further follower in the chain
        return leds

    @staticmethod
    def _mk(name: str, speed: float, steering: float, leds: dict) -> Decision:
        return Decision(state_name=name, base_speed=speed, steering=steering, leds=leds)
