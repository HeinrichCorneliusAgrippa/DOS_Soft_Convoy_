import math
import os
import time

import yaml

from tasks.project.packages.control import apply_leds, motors_from_decision
from tasks.project_follow.packages.fsm import STATE_TURN, FollowerFSM
from tasks.project_follow.packages.perception import FollowPerception

_CONFIG_FILE = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "config", "project_follow_config.yaml")
)


def _load_cfg() -> dict:
    try:
        with open(_CONFIG_FILE) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


# Live handles to the running FSM/perception, so the sim server's /tuning
# endpoint can adjust gains and speeds without restarting the agent.
live = {}


def main(camera, wheels, leds, stop_event,
         frame_queue=None, debug_lock=None, debug_dict=None, **_ignored):
    cfg = _load_cfg()
    perception = FollowPerception(cfg)
    fsm = FollowerFSM(cfg)
    live['fsm'] = fsm
    live['perception'] = perception

    last_state = None
    last_hw_warn = 0.0
    last_dbg = 0.0
    last_fps_update = 0.0
    frame_count = 0
    fps = 0.0
    cam_fail = 0
    in_maneuver = False
    man_pose0 = None   # pose at pursuit-turn entry (sim pose-odometry)

    try:
        while not stop_event.is_set():
            ok, frame = camera.read()
            if not ok:
                cam_fail += 1
                if cam_fail == 5:
                    print("[follow] camera returned no frames — check nvargus-daemon and "
                          "that no other process is holding the camera")
                # Auto-recover from a transient nvargus/caps hiccup that would
                # otherwise leave the video stuck on "Waiting for frames" forever.
                if cam_fail % 150 == 0:
                    print(f"[follow] re-initializing camera after {cam_fail} empty reads...")
                    try:
                        camera.stop()
                        camera.start()
                    except Exception as e:
                        print(f"[follow] camera re-init failed: {e}")
                time.sleep(0.02)
                continue
            if cam_fail:
                print(f"[follow] camera recovered after {cam_fail} empty reads")
                cam_fail = 0

            if frame_queue is not None:
                try:
                    frame_queue.put_nowait(frame.copy())
                except Exception:
                    pass

            now = time.monotonic()
            wm = perception.update(frame, now)

            # Pursuit-turn odometry: in sim the Godot pose (pushed over the
            # wheel channel) provides yaw/forward-distance since maneuver
            # entry, closing the loop on the corner turn. None on the real
            # robot -> the FSM falls back to timed maneuvers.
            pose = None
            gs = getattr(wheels, "game_state", None)
            if gs is not None and getattr(gs, "pose_x", None) is not None:
                pose = (gs.pose_x, gs.pose_z, gs.heading_rad)
            turn_yaw = None
            fwd_dist = None
            if in_maneuver and pose is not None and man_pose0 is not None:
                dth = pose[2] - man_pose0[2]
                turn_yaw = math.atan2(math.sin(dth), math.cos(dth))
                fwd_dist = math.hypot(pose[0] - man_pose0[0], pose[1] - man_pose0[1])

            decision = fsm.step(wm, turn_yaw_rad=turn_yaw, fwd_dist_m=fwd_dist)

            is_man = decision.state_name == STATE_TURN
            if is_man and not in_maneuver:
                man_pose0 = pose
            in_maneuver = is_man

            left, right = motors_from_decision(decision)

            frame_count += 1
            if now - last_fps_update > 1.0:
                fps = frame_count / (now - last_fps_update)
                frame_count = 0
                last_fps_update = now

            if debug_lock is not None and debug_dict is not None:
                dbg = perception.last_debug_info
                with debug_lock:
                    debug_dict.update({
                        'state': decision.state_name,
                        'base_speed': decision.base_speed,
                        'steering': decision.steering,
                        'left_speed': left,
                        'right_speed': right,
                        'leader_source': dbg.get('leader_source'),
                        'led_pair_px': dbg.get('led_pair_px'),
                        'grid_heading': dbg.get('grid_heading'),
                        'fps': fps,
                    })

            if now - last_dbg > 0.5:
                dbg = perception.last_debug_info
                print(f"[follow] {decision.state_name} "
                      f"src={dbg.get('leader_source')} span={dbg.get('led_pair_px')} "
                      f"hdg={dbg.get('grid_heading')} "
                      f"base={decision.base_speed:.2f} steer={decision.steering:+.2f} "
                      f"L={left:.2f} R={right:.2f}")
                last_dbg = now

            try:
                wheels.set_wheels_speed(left, right)
            except OSError as e:
                if now - last_hw_warn > 2.0:
                    print(f"[follow] wheels I/O error: {e} (check battery / HAT)")
                    last_hw_warn = now
            try:
                apply_leds(leds, decision)
            except OSError as e:
                if now - last_hw_warn > 2.0:
                    print(f"[follow] leds I/O error: {e}")
                    last_hw_warn = now

            if decision.state_name != last_state:
                print(f"[follow] state={decision.state_name} "
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
