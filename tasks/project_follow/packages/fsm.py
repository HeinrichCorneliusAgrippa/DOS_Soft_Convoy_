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
  PURSUIT_TURN - ("turn" corner_mode only) the marker swept sideways out of
               view: the lead turned a corner. Drive straight to where it
               vanished, execute our own ~90 deg turn in the observed
               direction (closed on pose odometry), then resume following.
               Needs odometry, so it is the sim default; the real bot uses
               the "lane" corner_mode below instead.
  LANE_FOLLOW- marker lost: cruise on the lane (it IS the leader's path). In the
               default "lane" corner_mode the steering is biased toward the
               remembered turn direction for a short commit window, so the bot
               commits to the corner the leader took instead of dead-reckoning a
               blind arc, until the leader is re-seen.
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
        # The perspective heading cue can catch it instead: |heading| above
        # this within the last sightings implies a turn. The cue is
        # best-effort / noisy (a 7x3 grid asymmetry, no camera intrinsics),
        # so it is gated behind turn_use_heading and its sign is configurable
        # via turn_heading_sign (see _infer_turn_dir). Producer convention
        # (marker_grid._heading / world_model.LeaderObs.heading): POSITIVE
        # heading = leader yawing right; turn_heading_sign maps that onto this
        # bot's camera so it can be flipped without touching the producer.
        self.turn_heading_min = float(cfg.get("turn_heading_min", 0.06))
        # Min |lane steering| while the leader is still visible to infer a corner
        # from the lane curve (FOLLOW keeps the marker centered, so lateral
        # rarely fires; the lane itself curves under the bot).
        self.turn_lane_steer_min = float(cfg.get("turn_lane_steer_min", 0.06))
        # Gate + sign for the perspective heading turn cue. Defaults preserve
        # the legacy sim behavior (cue on; the historical sim calibration where
        # negative raw heading was treated as a right turn). On the real bot
        # the cue is unreliable -> disable it in the config and rely on the
        # lateral-sweep cue, which keeps a consistent sign.
        self.turn_use_heading = bool(cfg.get("turn_use_heading", True))
        self.turn_heading_sign = float(cfg.get("turn_heading_sign", 1.0))
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

        # --- corner recovery mode ---
        # "lane": on losing the leader in a curve, remember which way it turned
        # and keep LANE-following (biased toward that side) until it is re-seen,
        # instead of dead-reckoning a blind arc. Robust on the real bot, which
        # has no pose odometry to close an open-loop turn. "turn": the legacy
        # ~90deg maneuver above (sim, where the Godot pose feeds the loop).
        self.corner_mode = str(cfg.get("corner_mode", "lane")).lower()
        # While lost, bias the lane steering toward the remembered corner for
        # this long (by then the lane markings themselves carry the curve).
        self.corner_commit_s = float(cfg.get("corner_commit_s", 3.0))
        # How hard to nudge toward the corner; added to the lane steering and
        # clamped to +/- max_steer (and to the base speed by the motor mixer).
        self.corner_steer_bias = float(cfg.get("corner_steer_bias", 0.12))

        self.rear_led_indices = list(cfg.get("rear_led_indices", [3, 4]))

        self._last_leader_t = -1e9
        self._last_lateral = 0.0
        self._last_span: Optional[float] = None
        self._last_heading: Optional[float] = None
        self._head_hist = deque(maxlen=24)   # (t, heading) of recent sightings
        self._lat_hist = deque(maxlen=24)    # (t, lateral) of recent sightings
        self._lane_steer_hist = deque(maxlen=24)  # (t, lane_steer, is_curve) while following
        self._armed = False
        # Active pursuit-turn maneuver: None, or
        # {'dir': 'left'|'right', 'phase': 'approach'|'turn', 't0': float,
        #  'approach_m': float}
        self._pturn: Optional[dict] = None
        self._settle_until = -1.0
        # Remembered turn direction while the leader is lost ('left'|'right'|None)
        # and the time until which the lane steering stays biased toward it.
        self._corner_dir: Optional[str] = None
        self._corner_until = -1.0
        # Latched once the leader is lost at point-blank range (grid undetectable
        # because its dots fill / leave the frame): hold a STOP so we never creep
        # into the leader's back. Cleared only when the leader is seen again.
        self._close_stop = False

    def step(self, wm: WorldModel, turn_yaw_rad: Optional[float] = None,
             fwd_dist_m: Optional[float] = None) -> Decision:
        t = wm.t
        leader = wm.leader
        confident = leader is not None and leader.score >= self.grid_min_score

        if confident:
            self._pturn = None  # leader back in sight -> just follow it
            # Only drop corner memory once we are clearly back on a straight
            # segment. A one-frame grid flicker mid-corner used to wipe
            # _corner_dir and strand REACQUIRE with zero bias.
            if abs(leader.lateral) < 0.10 and not wm.lane.is_curve:
                self._corner_dir = None
            self._close_stop = False  # leader resolvable again -> release the stop
            self._last_leader_t = t
            self._last_lateral = leader.lateral
            self._last_span = leader.pair_px if leader.pair_px is not None else leader.distance_px
            self._last_heading = leader.heading
            self._lat_hist.append((t, leader.lateral))
            if leader.heading is not None:
                self._head_hist.append((t, leader.heading))
            self._lane_steer_hist.append(
                (t, wm.lane.steering_suggestion, wm.lane.is_curve))
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

        # ---------- Marker lost ----------
        # Remember which way the leader went the instant it vanished, so the
        # recovery commits to that corner instead of re-guessing every frame
        # (the cue is freshest on the first lost frame and ages out after).
        if self._corner_dir is None:
            turn_dir = self._infer_turn_dir(t, wm)
            if turn_dir is not None:
                self._corner_dir = turn_dir
                self._corner_until = t + self.corner_commit_s

        # Lost at point-blank range: as the leader fills the frame its grid dots
        # leave view and the detection drops out. Latch a STOP so the follower
        # never creeps into the leader's back on a straight-line tailgate.
        # When the leader turned a corner at close range they are no longer
        # directly ahead — commit to corner recovery instead of parking.
        if (self._last_span is not None and self._last_span >= self.grid_close_px
                and self._corner_dir is None):
            self._close_stop = True
        if self._close_stop:
            return self._mk(STATE_CLOSE_STOP, 0.0, 0.0, all_leds(RED))

        if self.corner_mode == "turn":
            return self._lost_turn_mode(wm, t, turn_yaw_rad, fwd_dist_m)
        return self._lost_lane_mode(wm, t)

    def _lost_lane_mode(self, wm: WorldModel, t: float) -> Decision:
        """Default recovery: the leader is the lane, so keep following the lane,
        biased toward the remembered turn direction until the leader is re-seen.
        No open-loop arc -- a wrong direction guess only nudges the lane steering
        instead of spinning the bot off the road. The point-blank close-stop is
        handled in step() before this runs."""
        # While recently lost the leader is just around the corner: match its
        # pace, not cruise, or the follower overruns it mid-turn. Past the
        # window it is far -> cruise.
        in_pursuit = (t - self._last_leader_t) < self.pursuit_timeout_s
        bias = self._corner_bias(t, wm, in_pursuit)

        if wm.lane.healthy:
            steer = clamp(wm.lane.steering_suggestion + bias, -self.max_steer, self.max_steer)
            speed = self.pursuit_speed if in_pursuit else self.cruise_speed
            return self._mk(STATE_LANE, speed, steer, all_leds(YELLOW if bias else BLUE))

        # Lane washed out (tight corner / bad frame): inside the pursuit window
        # creep along the remembered curve instead of parking. The lane is
        # unhealthy *by definition* here, so its steering_suggestion is a
        # sparse, unreliable reading -- often the far or opposite lane glimpsed
        # across a corner (the lane detector only zeroes its own output well
        # below our health bar, so it still emits a confident-looking command
        # from a handful of pixels). Trusting it at creep speed makes the motor
        # mixer clamp steering to the tiny base speed and collapse into an
        # aggressive, frequently wrong-way pivot. Steer by the remembered corner
        # bias and/or the last marker bearing; never the unhealthy lane read.
        if in_pursuit:
            steer = self._reacquire_steer(t, wm, bias)
            return self._mk(STATE_REACQUIRE, self.reacquire_creep, steer, all_leds(YELLOW))
        return self._mk(STATE_HOLD, 0.0, 0.0, all_leds(OFF))

    def _lost_turn_mode(self, wm: WorldModel, t: float,
                        turn_yaw_rad: Optional[float],
                        fwd_dist_m: Optional[float]) -> Decision:
        """Legacy open-loop corner mimic (sim, closed on pose odometry): drive to
        where the marker vanished, arc ~90 deg the way it went, then resume."""
        # An active pursuit turn runs to completion (reappearance handled above).
        if self._pturn is not None:
            return self._run_pursuit_turn(wm, t, turn_yaw_rad, fwd_dist_m)

        # Just finished mimicking the corner: hold still while the leader pulls
        # away into detection range (tailgating keeps the grid larger than the
        # frame forever).
        if t < self._settle_until:
            return self._mk(STATE_CLOSE_STOP, 0.0, 0.0, all_leds(RED))

        within_grace = (t - self._last_leader_t) < self.leader_lost_grace_s
        if within_grace:
            # (Point-blank close-stop is handled in step() before this runs.)
            # Did the leader turn a corner? Mimic it: drive to where it
            # vanished, then turn the same way.
            if self._corner_dir is not None:
                gap_m = self.span_to_dist_k / max(self._last_span or 1.0, 1.0)
                self._pturn = {
                    'dir': self._corner_dir,
                    'phase': 'approach',
                    't0': t,
                    'approach_m': max(0.0, gap_m - self.turn_approach_margin_m),
                }
                return self._run_pursuit_turn(wm, t, turn_yaw_rad, fwd_dist_m)
            # Lost dead-ahead (washout): creep toward the last bearing.
            return self._mk(STATE_REACQUIRE, self.reacquire_creep,
                            self._reacquire_steer(t, wm, 0.0), all_leds(YELLOW))

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
            bias = self._corner_bias(t, wm, in_pursuit)
            return self._mk(STATE_REACQUIRE, self.reacquire_creep,
                            self._reacquire_steer(t, wm, bias), all_leds(YELLOW))
        return self._mk(STATE_HOLD, 0.0, 0.0, all_leds(OFF))

    def _corner_bias(self, t: float, wm: WorldModel, in_pursuit: bool) -> float:
        """Nudge steering toward the remembered corner. Bias normally decays after
        corner_commit_s once the lane carries the curve; while the lane is
        unhealthy during pursuit, keep biasing so REACQUIRE does not creep straight."""
        if self._corner_dir is None:
            return 0.0
        if t < self._corner_until or (in_pursuit and not wm.lane.healthy):
            return self.corner_steer_bias if self._corner_dir == "left" else -self.corner_steer_bias
        return 0.0

    def _reacquire_steer(self, t: float, wm: WorldModel, bias: float) -> float:
        """Steer during REACQUIRE: corner bias when known, else last marker bearing.
        Never blends in the unhealthy lane suggestion (see _lost_lane_mode)."""
        if abs(bias) >= 1e-9:
            return clamp(bias, -self.max_steer, self.max_steer)
        bearing_min = self.turn_lateral_min * 0.25  # commit even a slight off-center cue
        if abs(self._last_lateral) >= bearing_min:
            steer = lateral_to_steer(self._last_lateral, self.reacquire_steer_gain, self.max_steer)
            if self.heading_gain and self._last_heading is not None:
                steer = clamp(steer + self.heading_gain * self._last_heading,
                              -self.max_steer, self.max_steer)
            return steer
        return 0.0

    def _infer_turn_dir(self, t: float, wm: WorldModel) -> Optional[str]:
        """Which way did the leader go when the marker dropped? Lateral sweep
        is the geometric cue (consistent sign: marker swept right -> leader
        went right). FOLLOW keeps the marker centered, so on a slight turn the
        sweep stays below turn_lateral_min -- use the lane curve observed while
        following instead. The perspective heading cue is noisy/best-effort, so
        it is gated behind turn_use_heading and its sign aligned via
        turn_heading_sign."""
        if abs(self._last_lateral) >= self.turn_lateral_min:
            return 'right' if self._last_lateral > 0 else 'left'

        # FOLLOW keeps the marker centered, so |lateral| at the loss frame is
        # often below turn_lateral_min even though the leader swept sideways
        # a moment earlier. Use the peak |lateral| while still visible.
        recent_lat = [(ht, lat) for ht, lat in self._lat_hist if t - ht <= 0.6]
        if recent_lat:
            _, peak_lat = max(recent_lat, key=lambda x: abs(x[1]))
            if abs(peak_lat) >= self.turn_lateral_min:
                return 'right' if peak_lat > 0 else 'left'

        # Lane curve while the leader was still visible (reliable on real hardware
        # where heading is disabled and lateral stays near zero).
        recent_lane = [(ht, s, c) for ht, s, c in self._lane_steer_hist if t - ht <= 0.8]
        if recent_lane:
            curved = [s for _, s, c in recent_lane
                      if c and abs(s) >= self.turn_lane_steer_min]
            if curved:
                avg = sum(curved) / len(curved)
                return 'left' if avg > 0 else 'right'
            strong = [s for _, s, _ in recent_lane if abs(s) >= self.turn_lane_steer_min]
            if len(strong) >= 2 and all(s * strong[0] > 0 for s in strong):
                avg = sum(strong) / len(strong)
                return 'left' if avg > 0 else 'right'

        if wm.lane.is_curve:
            s = wm.lane.steering_suggestion
            if abs(s) >= self.turn_lane_steer_min:
                return 'left' if s > 0 else 'right'
            if wm.lane.curve_dir != 0:
                return 'left' if wm.lane.curve_dir > 0 else 'right'

        if not self.turn_use_heading:
            return None
        recent = [h for (ht, h) in self._head_hist if t - ht <= 0.8]
        if recent:
            best = max(recent, key=abs)
            if abs(best) >= self.turn_heading_min:
                signed = self.turn_heading_sign * best
                return 'right' if signed < 0 else 'left'
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
