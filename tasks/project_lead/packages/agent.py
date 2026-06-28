import math
import os
import time

import yaml

from tasks.project.packages.control import apply_deadzone, apply_leds, motors_from_decision
from tasks.project_lead.packages.fsm import (
    STATE_CROSS, STATE_TURN_L, STATE_TURN_R, LeadFSM,
)
from tasks.project_lead.packages.perception import LeadPerception

_CONFIG_FILE = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", "project_lead_config.yaml")
)

_MANEUVER_STATES = {STATE_TURN_L, STATE_TURN_R, STATE_CROSS}

# Live handles to the running FSM/perception, so the sim server's /tuning
# endpoint can adjust gains and speeds without restarting the agent.
live = {}


def _load_cfg() -> dict:
    try:
        with open(_CONFIG_FILE) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


def main(camera, wheels, leds, stop_event,
         frame_queue=None, debug_lock=None, debug_dict=None, encoders=None):
    cfg = _load_cfg()
    try:
        perception = LeadPerception(cfg)
    except Exception as e:
        print(f"[lead] perception init failed: {e!r} — streaming camera only, no control")
        perception = None

    # route_mode auto: decide turns at intersections from the map + live sim
    # pose instead of the fixed route list. Anything missing (no Godot scene,
    # no pose — e.g. on the real robot) falls back to the fixed route.
    navigator = None
    if str(cfg.get("route_mode", "fixed")).lower() == "auto":
        try:
            from tasks.project_lead.packages.navigator import TopoNavigator, load_sim_map
            map_data = load_sim_map()
            if map_data and map_data.get("tiles"):
                navigator = TopoNavigator(map_data, seed=cfg.get("auto_seed"))
                print("[lead] auto navigation: choosing turns from the map at intersections")
            else:
                print("[lead] route_mode=auto but no map available — using the fixed route")
        except Exception as e:
            print(f"[lead] auto navigation unavailable ({e!r}) — using the fixed route")
    fsm = LeadFSM(cfg, navigator=navigator)
    baseline = float(cfg.get("wheel_baseline_m", 0.1))
    # Real bot only: lift a wheel commanded to move but stuck below the motor
    # deadzone up to it, so the low-speed CURVE/LANE creep turns the wheels
    # instead of stalling on the spot. 0 disables (and the sim, which has a
    # pose, is gated out below regardless).
    wheel_deadzone = float(cfg.get("wheel_deadzone", 0.0))
    live['fsm'] = fsm
    live['perception'] = perception

    # Echo exactly what this run will execute at each intersection, so a stale /
    # mismatched deployed config (e.g. a route that doesn't match the repo) is
    # obvious in the console instead of being silently driven.
    print(f"[lead] route_mode={str(cfg.get('route_mode','fixed')).lower()} "
          f"navigator={'on' if navigator is not None else 'off'} "
          f"route={fsm.route}")
    print(f"[lead] maneuver_timed={fsm.maneuver_timed} "
          f"turn_time_s={fsm.turn_time_s} cross_time_s={fsm.cross_time_s}")

    last_state = None
    last_route_idx = fsm.route_idx
    last_hw_warn = 0.0
    last_dbg = 0.0
    last_fps_update = 0.0
    frame_count = 0
    fps = 0.0
    in_maneuver = False
    man_pose0 = None   # pose at maneuver entry (sim pose-odometry fallback)
    cam_fail = 0

    try:
        while not stop_event.is_set():
            ok, frame = camera.read()
            if not ok:
                cam_fail += 1
                if cam_fail == 5:
                    print("[lead] camera returned no frames — check nvargus-daemon and "
                          "that no other process is holding the camera")
                # Auto-recover from a transient nvargus/caps hiccup that would
                # otherwise leave the video stuck on "Waiting for frames" forever.
                if cam_fail % 150 == 0:
                    print(f"[lead] re-initializing camera after {cam_fail} empty reads...")
                    try:
                        camera.stop()
                        camera.start()
                    except Exception as e:
                        print(f"[lead] camera re-init failed: {e}")
                time.sleep(0.02)
                continue
            if cam_fail:
                print(f"[lead] camera recovered after {cam_fail} empty reads")
                cam_fail = 0

            # Queue the frame for the browser FIRST, before any vision/control
            # work. Everything below is wrapped so a perception/FSM error is
            # logged and skipped -- it can never kill the loop and freeze the
            # stream on "Waiting for frames".
            if frame_queue is not None:
                try:
                    frame_queue.put_nowait(frame.copy())
                except Exception:
                    pass

            now = time.monotonic()
            frame_count += 1
            if now - last_fps_update > 1.0:
                fps = frame_count / (now - last_fps_update)
                frame_count = 0
                last_fps_update = now

            if perception is None:
                time.sleep(0.005)        # no control pipeline; just stream frames
                continue

            try:
                wm = perception.update(frame, now)

                # Live pose (sim only: pushed by Godot over the wheel channel)
                # feeds the auto navigator and pose-odometry; None on the real
                # robot.
                pose = None
                gs = getattr(wheels, "game_state", None)
                if gs is not None and getattr(gs, "pose_x", None) is not None:
                    pose = (gs.pose_x, gs.pose_z, gs.heading_rad)

                # Odometry closes the loop on maneuvers: yaw for turns, forward
                # distance for straight crosses. Encoders on the real robot; in
                # sim the Godot pose provides the same signals (the timed turn
                # parameters are field-tuned and pirouette at sim wheel speeds).
                # None on both falls back to lane-reacquisition + timeout.
                turn_yaw = None
                fwd_dist = None
                odo_source = None
                if in_maneuver:
                    if encoders is not None:
                        try:
                            dl = encoders.left.distance_m()
                            dr = encoders.right.distance_m()
                            turn_yaw = (dr - dl) / max(baseline, 1e-3)
                            fwd_dist = 0.5 * (dl + dr)
                            odo_source = "encoders"
                        except Exception:
                            turn_yaw = None
                            fwd_dist = None
                    elif pose is not None and man_pose0 is not None:
                        dth = pose[2] - man_pose0[2]
                        turn_yaw = math.atan2(math.sin(dth), math.cos(dth))
                        fwd_dist = math.hypot(pose[0] - man_pose0[0], pose[1] - man_pose0[1])
                        odo_source = "pose"

                decision = fsm.step(wm, turn_yaw_rad=turn_yaw, fwd_dist_m=fwd_dist,
                                    pose=pose, odo_source=odo_source)
                if fsm.request_lane_reset:
                    perception.reset_lane()

                # Intersection fired this frame: route_idx advanced. Print the
                # exact step chosen and the maneuver it became, so a wrong route /
                # mis-selected turn is unmistakable in the console.
                if fsm.route_idx != last_route_idx:
                    print(f"[lead] >>> INTERSECTION FIRE: route {last_route_idx}->{fsm.route_idx}"
                          f"/{len(fsm.route)}  step={fsm.last_step!r}  -> {decision.state_name}")
                    last_route_idx = fsm.route_idx

                left, right = motors_from_decision(decision)
                # Real bot (no sim pose): keep a moving wheel above the motor
                # deadzone so the slow CURVE/LANE creep doesn't stall on the
                # spot. A commanded full stop stays at zero.
                if pose is None:
                    left, right = apply_deadzone(left, right, wheel_deadzone)

                # Reset odometry at maneuver entry so yaw integrates from zero.
                is_man = decision.state_name in _MANEUVER_STATES
                if is_man and not in_maneuver:
                    man_pose0 = pose
                    if encoders is not None:
                        try:
                            encoders.reset()
                            encoders.set_directions(True, True)
                        except Exception:
                            pass
                in_maneuver = is_man

                if debug_lock is not None and debug_dict is not None:
                    dbg = perception.last_debug_info
                    with debug_lock:
                        debug_dict.update({
                            'state': decision.state_name,
                            'base_speed': decision.base_speed,
                            'steering': decision.steering,
                            'left_speed': left,
                            'right_speed': right,
                            'apriltag_ids': dbg.get('apriltag_ids', []),
                            'apriltag_rejected': dbg.get('apriltag_rejected', 0),
                            'red_line': dbg.get('red_line'),
                            'route_idx': fsm.route_idx,
                            'route_step': fsm.last_step,
                            'fps': fps,
                        })

                if now - last_dbg > 0.5:
                    dbg = perception.last_debug_info
                    print(f"[lead] {decision.state_name} "
                          f"route={fsm.route_idx}/{len(fsm.route)} "
                          f"tags={dbg.get('apriltag_ids')} rej={dbg.get('apriltag_rejected')} "
                          f"redline={dbg.get('red_line')} "
                          f"base={decision.base_speed:.2f} steer={decision.steering:+.2f} "
                          f"L={left:.2f} R={right:.2f}")
                    last_dbg = now
            except Exception as e:
                if now - last_hw_warn > 2.0:
                    print(f"[lead] control error (camera still streaming): {e!r}")
                    last_hw_warn = now
                continue

            try:
                wheels.set_wheels_speed(left, right)
            except OSError as e:
                if now - last_hw_warn > 2.0:
                    print(f"[lead] wheels I/O error: {e} (check battery / HAT)")
                    last_hw_warn = now
            try:
                apply_leds(leds, decision)
            except OSError as e:
                if now - last_hw_warn > 2.0:
                    print(f"[lead] leds I/O error: {e}")
                    last_hw_warn = now

            if decision.state_name != last_state:
                print(f"[lead] state={decision.state_name} "
                      f"speed={decision.base_speed:.2f} steer={decision.steering:+.2f}")
                last_state = decision.state_name
    finally:
        try:
            wheels.set_wheels_speed(0.0, 0.0)
        except Exception:
            pass
        if leds is not None:
            try:
                leds.all_off()
            except Exception:
                pass
