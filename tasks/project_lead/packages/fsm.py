"""Lead-bot finite state machine: autonomous lane following with traffic-sign
slowing/stopping and a fixed programmed route executed at red-line intersections.

The lead follows no one. It:
  - lane-follows on straightaways,
  - enters CURVE (slowed) when the vision curve detector sees a bend ahead,
  - slows in SLOW zones (AprilTag SLOW signs),
  - remembers a STOP sign across the gap until the intersection's red line, halts
    ~1s, then executes the next route maneuver (turn left/right, cross straight,
    or final stop),
  - slows briefly after each maneuver so the follower can reacquire it.

Pure logic over a WorldModel + an optional encoder-derived yaw, so it is unit
testable without the vision stack or hardware.
"""
import math
from typing import Optional

from tasks.project.packages.fsm_common import (
    Decision, GREEN, RED, WHITE, YELLOW, all_leds, clamp,
)
from tasks.project.packages.world_model import WorldModel

STATE_LANE       = "LANE_FOLLOW"
STATE_CURVE      = "CURVE"
STATE_LOST       = "LANE_LOST"
STATE_STOP       = "STOP_AT_SIGN"
STATE_SLOW       = "SLOW_ZONE"
STATE_CROSS      = "CROSS_STRAIGHT"
STATE_TURN_L     = "TURN_LEFT"
STATE_TURN_R     = "TURN_RIGHT"
STATE_SLOW_AFTER = "SLOW_AFTER_TURN"
STATE_DONE       = "ROUTE_DONE"

_TURN_STEPS = ("left", "right")
_MANEUVER_STEPS = ("left", "right", "straight")


class LeadFSM:
    def __init__(self, cfg: Optional[dict] = None, navigator=None):
        cfg = cfg or {}
        # Optional map-aware navigator: when set and a pose is passed to
        # step(), it chooses the maneuver at each intersection instead of the
        # fixed route list (which remains the fallback).
        self.navigator = navigator
        self.cruise_speed = float(cfg.get("cruise_speed", 0.3))
        self.slow_factor  = float(cfg.get("slow_factor", 0.5))
        self.stop_duration = float(cfg.get("stop_duration", 1.0))
        self.slow_duration = float(cfg.get("slow_duration", 1.5))
        self.slow_cooldown = float(cfg.get("slow_cooldown", 3.0))
        # A STOP tag whose bbox covers >= this fraction of the frame is treated
        # as "close" and halts the bot directly (not only at a red line).
        self.stop_tag_near_area_frac = float(cfg.get("stop_tag_near_area_frac", 0.04))
        self.stop_tag_halt_s = float(cfg.get("stop_tag_halt_s", 1.0))

        self.route = [str(s).lower() for s in (cfg.get("route") or ["stop"])]

        # Maneuver shape / timing
        self.turn_base   = float(cfg.get("turn_base_speed", 0.25))
        self.turn_steer  = float(cfg.get("turn_steer", self.turn_base))
        # Left turns at a grid intersection sweep a wider arc than rights: scale
        # the left steer law down (and optionally its forward speed up).
        self.left_widen       = float(cfg.get("left_widen", 0.7))
        self.left_base_factor = float(cfg.get("left_base_factor", 1.0))
        self.cross_base  = float(cfg.get("cross_base_speed", 0.25))
        self.min_turn_s  = float(cfg.get("min_turn_s", 0.8))
        self.max_turn_s  = float(cfg.get("max_turn_s", 3.0))
        self.min_cross_s = float(cfg.get("min_cross_s", 0.4))
        self.max_cross_s = float(cfg.get("max_cross_s", 2.0))
        self.turn_yaw_target = float(cfg.get("turn_yaw_target_rad", 1.40))  # ~80 deg
        self.slow_after_turn_s = float(cfg.get("slow_after_turn_s", 2.0))
        self.slow_after_factor = float(cfg.get("slow_after_factor", 0.6))

        # Real-robot maneuvers: drive each turn / cross for a FIXED wall-clock
        # duration instead of ending on odometry or lane-reacquire. The
        # single-channel DB21 encoders give a noisy yaw/distance estimate, and
        # the lane reads "healthy" the moment the intersection's perpendicular /
        # exit markings enter frame, so the distance / heading / reacquire exits
        # all tripped after a brief moment and the bot bailed before completing
        # the turn. Sim keeps its exact, tuned pose-odometry closed loop — this
        # only engages when odo_source != "pose" (i.e. never in sim).
        self.maneuver_timed = bool(cfg.get("maneuver_timed", False))
        self.turn_time_s    = float(cfg.get("turn_time_s", 3.0))
        self.cross_time_s   = float(cfg.get("cross_time_s", 1.5))
        # Real bot only: a small fixed steer bias held during the straight cross
        # to cancel a physical wheel-imbalance drift (+ steer = left, - = right).
        # The wheel trim is multiplicative and tuned at lane speed; at the fixed
        # (higher) cross speed it leaves a residual veer. Gated to the timed/real
        # path below so the sim's ideal closed-loop cross stays at zero.
        self.cross_trim     = float(cfg.get("cross_trim", 0.0))

        # Slow down through map curves: the lane PD's steering differential
        # was tuned around the lane agent's own base speed — at full cruise
        # the same steer yields proportionally less curvature and the bot
        # runs wide across the center line on 90-deg curve tiles. Speed also
        # scales down continuously with steering demand (high demand = sharp
        # geometry or reacquisition, both need more curvature per meter).
        self.curve_speed_factor = float(cfg.get("curve_speed_factor", 0.3))
        self.steer_slowdown     = float(cfg.get("steer_slowdown", 2.5))
        self.steer_floor_factor = float(cfg.get("steer_floor_factor", 0.5))
        # Sim cross length (pose odometry): carry the bot across the WHOLE
        # unmarked intersection box before handing back to the lane PD —
        # cross_distance_m (field-tuned 0.35) releases it mid-box where there
        # are no markings to steer by, which is where it overshot the exit.
        self.cross_distance_sim_m = float(cfg.get("cross_distance_sim_m", 0.55))

        # Closed-loop odometry maneuver targets (used only when the agent passes
        # encoder distance/yaw; otherwise the timed values above are the fallback).
        self.cross_distance_m = float(cfg.get("cross_distance_m", 0.35))
        self.heading_kp       = float(cfg.get("maneuver_heading_kp", 0.6))
        self.turn_kp          = float(cfg.get("turn_kp", 0.8))
        self.cross_dist_tol_m = float(cfg.get("cross_dist_tol_m", 0.03))
        self.turn_yaw_tol_rad = float(cfg.get("turn_yaw_tol_rad", 0.08))

        # Sim turn geometry (pose odometry only). The field-tuned steer caps
        # assume real-motor deadzones; in the ideal simulator they pivot the
        # bot on the spot, dropping it off the lane so the lane controller
        # overshoots correcting. Steer for a target arc radius instead:
        # R = base * baseline / (2 * steer), platform-independent.
        self.baseline_m         = float(cfg.get("wheel_baseline_m", 0.10))
        self.turn_radius_left_m  = float(cfg.get("turn_radius_left_m", 0.40))
        self.turn_radius_right_m = float(cfg.get("turn_radius_right_m", 0.17))

        # Red-line intersection firing gates + clear latch
        self.fire_dist   = float(cfg.get("stopline_fire_dist", 0.45))
        self.fire_width  = float(cfg.get("stopline_fire_width", 0.40))
        self.clear_frames = int(cfg.get("stopline_clear_frames", 5))

        # Brief hold so the CURVE state doesn't flap when the detector
        # flickers frame to frame.
        self.curve_hold_s = float(cfg.get("curve_hold_s", 0.4))

        # Halt when the lane has been unhealthy this long while cruising:
        # without it the bot keeps driving blind at cruise speed straight off
        # the road (maneuvers are exempt — they are odometry/timer bound).
        self.lane_lost_stop_s = float(cfg.get("lane_lost_stop_s", 2.0))

        # Steering smoother (real bot only). At the real robot's low frame rate
        # the intersection's stop-line + exit markings entering the lane band
        # throw a one-frame steering spike right before CROSS_STRAIGHT; the motor
        # mixer (steer clamped to base_speed, and base_speed itself scaled DOWN by
        # raw steer in _steer_factor) turns that spike into a hard right wheel
        # differential — a visible jerk — then a snap to straight. Smoothing the
        # lane STEER INPUT (the value used for BOTH the speed scale and the steer)
        # bounds the per-frame change and lets it decay into the cross. Output-
        # only smoothing is inert here: base_speed is keyed off the raw steer, so
        # the clamp pins the wheels regardless. dt-aware first-order low-pass (Hz)
        # + a per-frame magnitude-slew cap. Gated to the REAL ROBOT (pose is None;
        # the Godot sim always supplies a pose) so the sim's tuned closed loop is
        # untouched — note odo_source is None in lane states on BOTH platforms, so
        # it cannot be the discriminator outside maneuvers.
        self.steer_lp_enabled = bool(cfg.get("steer_lp_enabled", True))
        self.steer_lp_hz      = float(cfg.get("steer_lp_hz", 1.0))
        # max(0): a negative rate would invert the slew clamp (lo > hi); 0 means
        # "no slew cap, low-pass only" (handled in _smooth_lane_steer).
        self.steer_slew_per_s = max(0.0, float(cfg.get("steer_slew_per_s", 0.55)))
        # Cap the dt the filter sees so a camera-frame dropout (a multi-second gap
        # with no step() calls) can't blow the slew ceiling open and let the jerk
        # back through on recovery. ~1.5 nominal frames at 3.8 FPS.
        self.steer_dt_max = float(cfg.get("steer_dt_max", 0.4))

        # mutable state
        self.route_idx = 0
        self._curve_until = -1.0
        self._stop_until = -1.0
        self._slow_until = -1.0
        self._slow_cooldown_until = -1.0
        self._stop_tag_cooldown_until = -1.0
        self._sign_pending = False
        self._maneuver: Optional[str] = None
        self._maneuver_start = 0.0
        self._slow_after_until = -1.0
        self._pending_step: Optional[str] = None
        self._consumed = False
        self._red_clear = 0
        self._done = False
        self._last_lane_ok = None
        self._steer_cmd = 0.0                 # last commanded (smoothed) lane steer
        self._steer_t: Optional[float] = None  # wall-clock of the last smoother update

        # surfaced for the agent/debug
        self.request_lane_reset = False
        self.last_step: Optional[str] = None

    # --- public ---------------------------------------------------------------
    def step(self, wm: WorldModel, turn_yaw_rad: Optional[float] = None,
             fwd_dist_m: Optional[float] = None,
             pose: Optional[tuple] = None,
             odo_source: Optional[str] = None) -> Decision:
        self.request_lane_reset = False
        t = wm.t
        # Smooth the lane steer only on the REAL robot. The discriminator is
        # `pose is None`: the Godot sim pushes a pose every frame, the real bot
        # never has one. (odo_source can't be used here — it is None in lane
        # states on BOTH platforms, only set inside maneuvers.) Computed once and
        # threaded into _run_maneuver so the whole step shares one gate.
        smooth_on = self.steer_lp_enabled and pose is None
        self._update_latch(wm)
        self._ingest_signs(wm)

        # Lane-lost clock: measures unhealthy time only while CRUISING. During
        # a maneuver / halt / slow-after window the bot legitimately sees no
        # markings (intersection box), so the clock is pinned there — cruising
        # resumes with the full grace period to reacquire the lane instead of
        # halting on the first frame after the window expires.
        busy = (self._maneuver is not None or self._pending_step is not None
                or t < self._stop_until or t < self._slow_after_until)
        if self._last_lane_ok is None or wm.lane.healthy or busy:
            self._last_lane_ok = t

        if self._done:
            return self._halt(STATE_DONE, t)

        # 1) an active maneuver takes precedence
        if self._maneuver is not None:
            return self._run_maneuver(wm, t, turn_yaw_rad, fwd_dist_m, odo_source, smooth_on)

        # 2) halting at the line for a pending STOP
        if t < self._stop_until:
            return self._halt(STATE_STOP, t)
        if self._pending_step is not None:
            step = self._pending_step
            self._pending_step = None
            self._begin_step(step, t)
            if self._done:
                return self._halt(STATE_DONE, t)
            return self._run_maneuver(wm, t, turn_yaw_rad, fwd_dist_m, odo_source, smooth_on)

        # 3) slow-after-turn window (give the follower time to reacquire).
        #    Scale further with steering demand: this is exactly the lane
        #    re-entry where a hot PD correction overshoots the markings.
        if t < self._slow_after_until:
            steer = self._smooth_lane_steer(wm.lane.steering_suggestion, t, smooth_on)
            return self._decide(STATE_SLOW_AFTER,
                                self.cruise_speed * self.slow_after_factor * self._steer_factor(steer),
                                steer, YELLOW)

        # 4) intersection event -> choose the next step: the map-aware
        #    navigator decides from the live pose when available, otherwise
        #    consume the fixed route list.
        if self._intersection_fires(wm):
            self._consumed = True
            self._red_clear = 0
            step = None
            if self.navigator is not None and pose is not None:
                step = self.navigator.next_step(*pose)
            if step is None:
                step = self.route[self.route_idx] if self.route_idx < len(self.route) else "stop"
            self.route_idx += 1
            self.last_step = step
            if self._sign_pending:
                self._sign_pending = False
                self._stop_until = t + self.stop_duration
                self._pending_step = step  # maneuver begins after the 1s halt
                return self._halt(STATE_STOP, t)
            self._begin_step(step, t)
            if self._done:
                return self._halt(STATE_DONE, t)
            return self._run_maneuver(wm, t, turn_yaw_rad, fwd_dist_m, odo_source, smooth_on)

        # 5) lane lost while cruising: hold position instead of driving blind
        #    (clock maintained at the top of step()).
        if t - self._last_lane_ok > self.lane_lost_stop_s:
            return self._halt(STATE_LOST, t)

        # 6) slow zone (timed)
        if t < self._slow_until:
            steer = self._smooth_lane_steer(wm.lane.steering_suggestion, t, smooth_on)
            return self._decide(STATE_SLOW, self.cruise_speed * self.slow_factor,
                                steer, YELLOW)

        # 7) curve approach: the detector is vision-based (lane-marking
        #    spread), so it fires while the bend is still ahead — slow into
        #    and through it as a dedicated state. Held briefly so a
        #    flickering detection doesn't flap the state.
        steer = self._smooth_lane_steer(wm.lane.steering_suggestion, t, smooth_on)
        if wm.lane.is_curve:
            self._curve_until = t + self.curve_hold_s
        if t < self._curve_until:
            factor = min(self.curve_speed_factor, self._steer_factor(steer))
            return self._decide(STATE_CURVE, self.cruise_speed * factor, steer, YELLOW)

        # 8) default: lane following; speed still scales with steering demand
        #    (reacquire wobbles and the like).
        return self._decide(STATE_LANE, self.cruise_speed * self._steer_factor(steer),
                            steer, GREEN)

    def _steer_factor(self, steer: float) -> float:
        """Continuous slowdown with steering demand, floored so the bot
        always keeps moving."""
        return clamp(1.0 - self.steer_slowdown * abs(steer),
                     self.steer_floor_factor, 1.0)

    def _smooth_lane_steer(self, raw: float, t: float, smooth_on: bool) -> float:
        """Smooth the lane steer command (real bot only) so the pre-intersection
        spike can't snap the wheels sideways and the steer decays into CROSS.
        Called exactly once per step() — in the single terminal branch that fires
        — so the dt below is always one real frame. When off, passes raw through
        and keeps the filter warm (reseeds) so a later smoothed frame has no step.

        Hybrid: a dt-aware first-order low-pass (carries the ramp tail) wrapped by
        a dt-aware per-frame magnitude-slew cap (bounds the wheel-diff step, which
        the low-pass alone cannot at this frame rate)."""
        if (not smooth_on) or self.steer_lp_hz <= 0.0:
            return self._reseed_steer(raw, t)
        # Cap dt so a frame dropout (large gap) can't open the slew ceiling and
        # let the jerk back through on recovery.
        dt = 0.0 if self._steer_t is None else clamp(t - self._steer_t, 0.0, self.steer_dt_max)
        self._steer_t = t
        # dt-aware low-pass; alpha == 0 at dt == 0 so the first frame just holds.
        alpha = 1.0 - math.exp(-2.0 * math.pi * self.steer_lp_hz * dt)
        target = self._steer_cmd + alpha * (raw - self._steer_cmd)
        # Per-frame magnitude-slew cap on top of the low-pass. Skipped when the
        # rate is non-positive (0 = low-pass only; avoids an inverted clamp).
        if self.steer_slew_per_s > 0.0:
            max_step = self.steer_slew_per_s * dt
            target = self._steer_cmd + clamp(target - self._steer_cmd, -max_step, max_step)
        self._steer_cmd = target
        return self._steer_cmd

    def _reseed_steer(self, value: float, t: float) -> float:
        """Hold the smoother at a value emitted by a bypass branch (the fixed
        turn arc, or a halt) so the next smoothed lane frame resumes from it
        without a step. Returns the value for convenient inline use."""
        self._steer_cmd = value
        self._steer_t = t
        return value

    def _halt(self, name: str, t: float) -> Decision:
        """A stopped state (DONE / STOP / LOST): zero speed and steer, and reseed
        the smoother to 0 so a resumed lane frame ramps up from straight."""
        self._reseed_steer(0.0, t)
        return self._decide(name, 0.0, 0.0, RED)

    # --- internals ------------------------------------------------------------
    def _ingest_signs(self, wm: WorldModel) -> None:
        kinds = {s.kind for s in wm.signs}
        if "STOP" in kinds:
            self._sign_pending = True  # remembered until the intersection consumes it
            # A STOP tag that fills enough of the frame is close: halt directly so
            # the bot reacts to the sign itself, not only at the intersection red
            # line (symmetric with SLOW acting immediately). Gated on bbox size so
            # a far / handheld tag doesn't trip it, and cooled down so it doesn't
            # re-arm every frame while the sign stays in view.
            frame_area = max(1, wm.frame_w * wm.frame_h)
            stop_area = max((max(0, s.bbox[2] - s.bbox[0]) * max(0, s.bbox[3] - s.bbox[1])
                             for s in wm.signs if s.kind == "STOP"), default=0)
            if (stop_area / frame_area >= self.stop_tag_near_area_frac
                    and wm.t >= self._stop_tag_cooldown_until):
                self._stop_until = wm.t + self.stop_tag_halt_s
                self._stop_tag_cooldown_until = (
                    wm.t + self.stop_tag_halt_s + self.slow_cooldown)
        if "SLOW" in kinds and wm.t >= self._slow_cooldown_until:
            self._slow_until = wm.t + self.slow_duration
            self._slow_cooldown_until = wm.t + self.slow_duration + self.slow_cooldown

    def _update_latch(self, wm: WorldModel) -> None:
        rl = wm.red_line
        red_present = rl is not None and rl.present and rl.width_frac >= (self.fire_width * 0.5)
        # Only count "line cleared" frames once we've finished reacting to the
        # last intersection. While a maneuver / stop / slow-after is in progress
        # the bot is still on top of the same physical line; counting clear
        # frames here would re-arm _consumed mid-reaction and let that one line
        # re-fire, burning the whole route down to the terminal 'stop'.
        busy = (self._maneuver is not None
                or wm.t < self._slow_after_until
                or wm.t < self._stop_until)
        if red_present:
            self._red_clear = 0
        elif not busy:
            self._red_clear += 1
            if self._consumed and self._red_clear >= self.clear_frames:
                self._consumed = False  # cleared the line -> ready for next intersection

    def _intersection_fires(self, wm: WorldModel) -> bool:
        rl = wm.red_line
        if rl is None or not rl.present or self._consumed:
            return False
        return rl.dist_proxy >= self.fire_dist and rl.width_frac >= self.fire_width

    def _begin_step(self, step: str, t: float) -> None:
        if step == "stop":
            self._done = True
            self._maneuver = None
            return
        self._maneuver = step if step in _MANEUVER_STEPS else "straight"
        self._maneuver_start = t

    def _run_maneuver(self, wm: WorldModel, t: float,
                      turn_yaw_rad: Optional[float],
                      fwd_dist_m: Optional[float],
                      odo_source: Optional[str] = None,
                      smooth_on: bool = False) -> Decision:
        step = self._maneuver
        elapsed = t - self._maneuver_start
        is_turn = step in _TURN_STEPS
        min_s = self.min_turn_s if is_turn else self.min_cross_s
        max_s = self.max_turn_s if is_turn else self.max_cross_s

        # Closed-loop on encoder odometry when both scalars are available;
        # otherwise fall back to the legacy timed / lane-reacquire behaviour.
        have_odo = turn_yaw_rad is not None and fwd_dist_m is not None

        # Real robot: end the maneuver on a fixed wall-clock duration (see
        # __init__). Sim (odo_source == "pose") keeps the closed loop below.
        # `smooth_on` is passed in from step() (gated on the real robot), so the
        # whole step shares one gate instead of recomputing a different one here.
        timed = self.maneuver_timed and odo_source != "pose"

        # In sim, carry the cross through the whole unmarked intersection box
        # (pose odometry is exact); the field-tuned distance stays for real.
        cross_target = self.cross_distance_sim_m if odo_source == "pose" else self.cross_distance_m

        done = False
        if timed:                                              # fixed-duration maneuver
            done = elapsed >= (self.turn_time_s if is_turn else self.cross_time_s)
        elif elapsed >= max_s:                                 # hard safety timeout
            done = True
        elif have_odo:
            if is_turn and abs(turn_yaw_rad) >= (self.turn_yaw_target - self.turn_yaw_tol_rad):
                done = True                                    # turned to target heading
            elif (not is_turn) and fwd_dist_m >= (cross_target - self.cross_dist_tol_m):
                done = True                                    # crossed the target distance
        elif is_turn and turn_yaw_rad is not None and abs(turn_yaw_rad) >= self.turn_yaw_target:
            done = True                                        # encoder yaw target reached
        elif elapsed >= min_s and wm.lane.healthy:
            done = True                                        # lane reacquired -> self-correct

        if done:
            self._maneuver = None
            self._slow_after_until = t + self.slow_after_turn_s
            self.request_lane_reset = True  # agent clears stale lane PID on re-entry
            steer = self._smooth_lane_steer(wm.lane.steering_suggestion, t, smooth_on)
            return self._decide(STATE_SLOW_AFTER, self.cruise_speed * self.slow_after_factor,
                                steer, YELLOW)

        if is_turn:
            # Lane convention: +steer turns LEFT, -steer turns RIGHT.
            sign = 1.0 if step == "left" else -1.0
            # Left turns sweep a wider arc than rights at a grid intersection:
            # scale the whole steer law (ceiling + floor) down for lefts so the
            # radius opens out, and optionally raise the forward speed.
            widen       = self.left_widen if step == "left" else 1.0
            steer_cap   = self.turn_steer * widen
            steer_floor = 0.10 * widen
            base        = self.turn_base * (self.left_base_factor if step == "left" else 1.0)
            if odo_source == "pose":
                # Simulation: the field-tuned caps above assume real-motor
                # deadzones and pivot the ideal sim bot on the spot, dropping
                # it off the lane. Steer for the intersection's actual arc
                # radius instead (R = base*baseline/(2*steer)) so the turn
                # SWEEPS onto the exit lane and the lane controller re-enters
                # with a small error instead of overshooting the markings.
                radius = self.turn_radius_left_m if step == "left" else self.turn_radius_right_m
                steer_cap = clamp(base * self.baseline_m / (2.0 * max(radius, 1e-3)),
                                  0.02, self.turn_steer)
                steer_floor = 0.45 * steer_cap
            if have_odo and self.turn_yaw_target > 0 and not timed:
                # Taper steer as the remaining yaw error shrinks, with a floor so
                # the bot keeps rotating until it reaches the target heading.
                yaw_err = self.turn_yaw_target - abs(turn_yaw_rad)
                mag = clamp(steer_cap * self.turn_kp * yaw_err / self.turn_yaw_target,
                            steer_floor, steer_cap)
                steer = sign * mag
            else:
                # Timed (real robot) or no odometry: hold a fixed steady arc for
                # the whole duration — we don't trust the encoder yaw to taper.
                steer = sign * steer_cap
            name = STATE_TURN_L if step == "left" else STATE_TURN_R
            # The turn arc is passed through UNCHANGED so the field-tuned timed
            # swept angle is exact; reseed the smoother to it so the SLOW_AFTER
            # exit relaxes from the turn steer instead of stepping.
            self._reseed_steer(steer, t)
            return self._decide(name, base, steer, WHITE)

        # Straight cross: hold heading with a small P term on yaw drift.
        # yaw > 0 means drifting left -> negative steer corrects back right.
        # In timed mode the encoder yaw isn't trusted, so just drive straight,
        # holding a small fixed cross_trim to cancel the real bot's straight-cross
        # drift (gated to timed so the sim cross stays dead straight at 0).
        raw_cross = 0.0
        if have_odo and not timed:
            raw_cross = clamp(-self.heading_kp * turn_yaw_rad, -self.turn_steer, self.turn_steer)
        elif timed:
            raw_cross = self.cross_trim
        # Run the cross target through the SAME smoother so the steer carried in
        # from the lane approach (the spike) decays smoothly to it — this is what
        # turns the "jerk right then snap straight" into a clean ramp.
        steer = self._smooth_lane_steer(raw_cross, t, smooth_on)
        return self._decide(STATE_CROSS, self.cross_base, steer, WHITE)

    @staticmethod
    def _decide(name: str, speed: float, steering: float, color) -> Decision:
        return Decision(state_name=name, base_speed=speed,
                        steering=clamp(steering, -1.0, 1.0), leds=all_leds(color))
