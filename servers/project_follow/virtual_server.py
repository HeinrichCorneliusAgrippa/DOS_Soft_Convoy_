import sys
import os
import json
import threading
import argparse
import time

script_dir   = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.join(script_dir, '..', '..')
sys.path.insert(0, project_root)

from flask import Flask, Response, jsonify, render_template_string, request
import numpy as np
import cv2

from duckiebot.camera_driver.godot_camera_driver import GodotCameraDriver, GodotCameraConfig
from duckiebot.wheel_driver.godot_wheels_driver import GodotWheelsDriver
from duckiebot.wheel_driver.wheels_driver_abs import WheelPWMConfiguration
from launcher.ports import find_available_port
from launcher.config import GODOT_SCENES
from servers.common import (LatestFrame, handle_hsv_tuning, shutdown_cleanup,
                            suppress_http_logs, update_yaml_values)
from servers.sim_map import load_map_for_scene
from servers.templates.convoy import get_template

import tasks.project_follow.packages.agent as agent

HTML_TEMPLATE = get_template('follow', 'Convoy — Follower Bot', 'Godot Simulation')

app        = Flask(__name__)
camera     = None
wheels     = None
stop_event = threading.Event()

_agent_thread = None
_agent_lock   = threading.Lock()

_frame_queue     = LatestFrame()
_debug_info_lock = threading.Lock()
_debug_info      = {}

_MAP_DATA    = load_map_for_scene(GODOT_SCENES.get('project_follow', ''))
_CONFIG_FILE = os.path.normpath(os.path.join(script_dir, '..', '..', 'config', 'project_follow_config.yaml'))

# Span thresholds for the UI gauge (defaults mirror FollowerFSM's).
_LEADER_CFG = {'grid_safe_px': 18.0, 'grid_stop_px': 70.0, 'grid_arm_px': 60.0}

# Lateral offset of the leader as seen by the server-side grid tracker (the
# agent's debug dict doesn't carry it); [-1, 1], None when the grid is lost.
# The tracker is stateful and each connected /video client renders frames on
# its own thread, so all tracker access is serialized; the timestamp lets
# /status report None instead of a value frozen since the last viewer left.
_grid_lateral = None
_grid_lateral_ts = 0.0
_grid_lock = threading.Lock()

# Optional 2x2 debug montage: annotated camera + yellow / white lane masks and
# the circle-grid leader detection, same panels as the real follower server.
_SHOW_MASKS = True
_PANEL_SIZE = (320, 240)
_STREAM_QUALITY = 60
try:
    import yaml as _yaml
    from tasks.visual_lane_servoing.packages.visual_servoing_activity import detect_lane_markings
    from tasks.project.packages.marker_grid import MarkerGridTracker
    try:
        with open(_CONFIG_FILE) as _cf:
            _fcfg = _yaml.safe_load(_cf) or {}
        for _k in _LEADER_CFG:
            if _fcfg.get(_k) is not None:
                _LEADER_CFG[_k] = float(_fcfg[_k])
    except Exception:
        _fcfg = {}
    _grid_tracker = MarkerGridTracker(cfg=_fcfg)
    _MASKS_AVAILABLE = True
except Exception as _mask_err:
    print(f'[follow sim] mask preview disabled: {_mask_err}')
    _MASKS_AVAILABLE = False


def _mask_to_bgr(mask, color, label):
    out = np.zeros((mask.shape[0], mask.shape[1], 3), dtype=np.uint8)
    out[mask > 0] = color
    cv2.putText(out, label, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return out


def _grid_panel(panel):
    global _grid_lateral, _grid_lateral_ts
    vis = panel.copy()
    try:
        with _grid_lock:
            obs = _grid_tracker.update(panel)
            if obs is not None:
                w = panel.shape[1]
                _grid_lateral = round(float(obs.midpoint[0] - w / 2.0) / (w / 2.0), 2)
            else:
                _grid_lateral = None
            _grid_lateral_ts = time.time()
        if obs is not None:
            x1, y1, x2, y2 = obs.bbox
            cv2.rectangle(vis, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
            cx, cy = obs.midpoint
            cv2.circle(vis, (int(cx), int(cy)), 4, (0, 0, 255), -1)
    except Exception:
        with _grid_lock:
            _grid_lateral = None
            _grid_lateral_ts = time.time()
    cv2.putText(vis, 'GRID (leader)', (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return vis


def _montage(frame_bgr):
    panel = cv2.resize(frame_bgr, _PANEL_SIZE, interpolation=cv2.INTER_AREA)
    cam = visualize(panel)
    z = np.zeros((_PANEL_SIZE[1], _PANEL_SIZE[0]), dtype=np.uint8)
    try:
        m_yellow, m_white = detect_lane_markings(panel)
    except Exception:
        m_yellow = m_white = z
    yellow = _mask_to_bgr(m_yellow, (0, 255, 255), 'YELLOW centerline')
    white  = _mask_to_bgr(m_white,  (255, 255, 255), 'WHITE edge')
    grid   = _grid_panel(panel)
    return cv2.vconcat([cv2.hconcat([cam, yellow]), cv2.hconcat([white, grid])])


def visualize(frame_bgr):
    if frame_bgr is None:
        blank = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(blank, 'Waiting for camera...', (160, 240),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (80, 80, 80), 2)
        return blank

    display = frame_bgr.copy()
    h, w = display.shape[:2]
    with _debug_info_lock:
        info = _debug_info.copy()
    font = cv2.FONT_HERSHEY_SIMPLEX

    cv2.putText(display, f"State: {info.get('state', 'INIT')}", (10, 25), font, 0.6, (0, 255, 0), 2)
    cv2.putText(display, f"Base: {info.get('base_speed', 0.0):.2f} "
                         f"Steer: {info.get('steering', 0.0):+.2f}", (10, 50), font, 0.5, (0, 255, 0), 1)
    cv2.putText(display, f"L: {info.get('left_speed', 0.0):+.2f}  "
                         f"R: {info.get('right_speed', 0.0):+.2f}", (10, 70), font, 0.5, (0, 255, 0), 1)

    src = info.get('leader_source')
    if src:
        span = info.get('led_pair_px')
        text = f'Leader: {src}'
        if span:
            text += f' (span={span}px)'
        hdg = info.get('grid_heading')
        if hdg is not None:
            text += f' hdg={hdg}'
        cv2.putText(display, text, (10, h - 40), font, 0.5, (255, 255, 0), 1)
    cv2.putText(display, f"{info.get('fps', 0.0):.1f} FPS", (w - 100, 25), font, 0.5, (200, 200, 200), 1)
    return display


def generate_frames():
    while True:
        frame = _frame_queue.get_latest()
        try:
            if frame is None:
                display = visualize(None)
            elif _SHOW_MASKS and _MASKS_AVAILABLE:
                display = _montage(frame)
            else:
                display = visualize(frame)
            ret, jpeg = cv2.imencode('.jpg', display, [cv2.IMWRITE_JPEG_QUALITY, _STREAM_QUALITY])
            if ret:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
        except Exception as e:
            print(f'[VideoStream] render error: {e}')
        time.sleep(0.03)   # ~30 fps cap


# --- agent lifecycle ---------------------------------------------------------

def _agent_alive():
    return _agent_thread is not None and _agent_thread.is_alive()


def _start_agent():
    """Start the follower agent thread (sim has no LEDs -> leds=None; the
    follower takes no encoders)."""
    global _agent_thread
    with _agent_lock:
        if _agent_alive():
            return False
        stop_event.clear()
        _agent_thread = threading.Thread(
            target=agent.main,
            args=(camera, wheels, None, stop_event, _frame_queue, _debug_info_lock, _debug_info),
            daemon=True,
            name='FollowAgentThread',
        )
        _agent_thread.start()
        return True


def _stop_agent(timeout=3.0):
    """Returns (was_running, stopped_now). stopped_now can be False when the
    agent thread is wedged past the join timeout (e.g. blocked in the camera
    driver's accept while Godot is down) — report that instead of lying."""
    with _agent_lock:
        if not _agent_alive():
            return False, True
        stop_event.set()
        _agent_thread.join(timeout)
        return True, not _agent_thread.is_alive()


# --- routes ------------------------------------------------------------------

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, map_json=json.dumps(_MAP_DATA))


@app.route('/video')
def video():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/frame.jpg')
def frame_jpg():
    """Latest raw full-resolution camera frame (debug / offline analysis)."""
    frame = _frame_queue.get_latest()
    if frame is None:
        return jsonify(error='no frame yet'), 503
    ok, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        return jsonify(error='encode failed'), 500
    return Response(jpeg.tobytes(), mimetype='image/jpeg')


@app.route('/status')
def status():
    running = _agent_alive()
    if wheels is not None and not running:
        # The agent's send_wheels normally drains the Godot socket; while it
        # is paused, poll here so pose/game state stay fresh.
        wheels.is_game_over()
    pose = None
    leader_pose = None
    game = {}
    if wheels is not None:
        gs = wheels.game_state
        if gs.pose_x is not None:
            pose = {'x': gs.pose_x, 'z': gs.pose_z, 'theta': gs.heading_rad}
        if getattr(gs, 'npc_x', None) is not None:
            leader_pose = {'x': gs.npc_x, 'z': gs.npc_z}
        game = {
            'game_over': gs.game_over,
            'survival_time': round(gs.survival_time, 1),
            'total_distance': round(gs.distance_traveled, 2),
            'collision_duck': gs.collision_duck,
        }
    with _debug_info_lock:
        info = dict(_debug_info)
    with _grid_lock:
        fresh = (time.time() - _grid_lateral_ts) < 1.0
        info['grid_lateral'] = _grid_lateral if fresh else None
    return jsonify({
        'agent': info,
        'agent_running': running,
        'pose': pose,
        'leader_pose': leader_pose,
        'game': game,
        'leader_cfg': _LEADER_CFG,
    })


@app.route('/sample')
def sample():
    """Click-to-sample HSV. fx/fy are click fractions over the streamed image;
    with the 2x2 montage the camera is the top-left quarter. Returns median
    H/S/V of a small patch in the same BGR->HSV space the detectors use."""
    try:
        fx = float(request.args.get('fx', -1.0))
        fy = float(request.args.get('fy', -1.0))
    except ValueError:
        return jsonify(error='bad coords')
    frame = _frame_queue.get_latest()
    frame = None if frame is None else frame.copy()
    if frame is None:
        return jsonify(error='no frame yet')

    if _SHOW_MASKS and _MASKS_AVAILABLE:
        if not (0.0 <= fx < 0.5 and 0.0 <= fy < 0.5):
            return jsonify(hint='click the TOP-LEFT camera panel')
        sx, sy = fx * 2.0, fy * 2.0
    else:
        sx, sy = fx, fy
    if not (0.0 <= sx <= 1.0 and 0.0 <= sy <= 1.0):
        return jsonify(error='out of range')

    h_img, w_img = frame.shape[:2]
    px = min(w_img - 1, max(0, int(sx * w_img)))
    py = min(h_img - 1, max(0, int(sy * h_img)))
    r = 3
    patch = frame[max(0, py - r):py + r + 1, max(0, px - r):px + r + 1]
    hsv = cv2.cvtColor(patch, cv2.COLOR_BGR2HSV)
    return jsonify(
        h=int(np.median(hsv[:, :, 0])),
        s=int(np.median(hsv[:, :, 1])),
        v=int(np.median(hsv[:, :, 2])),
        px=px, py=py,
    )


_LANE_CONFIG_FILE = os.path.normpath(os.path.join(script_dir, '..', '..', 'config', 'lane_servoing_config.yaml'))
_HSV_CONFIG_FILE  = os.path.normpath(os.path.join(script_dir, '..', '..', 'config', 'lane_servoing_hsv_config.yaml'))


@app.route('/hsv', methods=['GET', 'POST'])
def hsv_tuning():
    return jsonify(handle_hsv_tuning(request, _HSV_CONFIG_FILE))

# UI tuning bounds: (min, max)
_TUNE_BOUNDS = {'speed': (0.05, 0.6), 'kp': (0.0, 1.0), 'kd': (0.0, 2.0)}


@app.route('/tuning', methods=['GET', 'POST'])
def tuning():
    """Live-tune cruise speed and the lane PD gains. Applies to the running
    agent immediately and persists to the YAML configs so restarts keep it."""
    live = getattr(agent, 'live', {})
    fsm = live.get('fsm')
    lane = getattr(live.get('perception'), 'lane', None)

    if request.method == 'GET':
        return jsonify(
            speed=getattr(fsm, 'cruise_speed', None),
            kp=getattr(lane, 'p_gain', None),
            kd=getattr(lane, 'd_gain', None),
        )

    body = request.get_json(silent=True) or {}
    applied = {}
    for key in ('speed', 'kp', 'kd'):
        if body.get(key) is None:
            continue
        try:
            lo, hi = _TUNE_BOUNDS[key]
            applied[key] = min(hi, max(lo, float(body[key])))
        except (TypeError, ValueError):
            return jsonify(status='error', message=f'bad value for {key}')
    if not applied:
        return jsonify(status='error', message='nothing to apply')

    if 'speed' in applied:
        if fsm is not None:
            fsm.cruise_speed = applied['speed']
        update_yaml_values(_CONFIG_FILE, {'cruise_speed': applied['speed']})
    lane_updates = {}
    if 'kp' in applied:
        if lane is not None:
            lane.p_gain = applied['kp']
        lane_updates['p_gain'] = applied['kp']
    if 'kd' in applied:
        if lane is not None:
            lane.d_gain = applied['kd']
        lane_updates['d_gain'] = applied['kd']
    if lane_updates:
        update_yaml_values(_LANE_CONFIG_FILE, lane_updates)

    live_note = '' if fsm is not None else ' (agent not running: saved to config only)'
    return jsonify(status='ok',
                   message='applied ' + ', '.join(f'{k}={v:g}' for k, v in applied.items()) + live_note)


@app.route('/agent/start', methods=['POST'])
def agent_start():
    started = _start_agent()
    return jsonify(status='ok', message='agent started' if started else 'agent already running')


@app.route('/agent/stop', methods=['POST'])
def agent_stop():
    was_running, stopped = _stop_agent()
    if not was_running:
        return jsonify(status='ok', message='agent not running')
    if not stopped:
        return jsonify(status='error', message='agent is still stopping (camera reconnect in progress) — wheels are zeroed')
    return jsonify(status='ok', message='agent paused')


@app.route('/reset', methods=['POST'])
def reset():
    """Respawn the robot in Godot and restart the agent with a fresh FSM.
    The Godot reset also re-parks the NPC leader at the start of its loop,
    so the follower re-arms with the same geometry as a fresh boot."""
    global _grid_lateral
    _, stopped = _stop_agent()
    if not stopped:
        return jsonify(status='error', message='agent is busy stopping; try again in a few seconds')
    if wheels is not None:
        wheels.reset_game()
    with _debug_info_lock:
        _debug_info.clear()
    with _grid_lock:
        _grid_lateral = None
    time.sleep(0.2)
    if wheels is not None:
        # Drop state pushed before the respawn so /status can't serve a
        # pre-reset pose or a stale game_over.
        wheels.clear_state()
    _start_agent()
    return jsonify(status='ok', message='simulation reset')


@app.route('/command', methods=['POST'])
def command():
    return jsonify({'status': 'ok'})


@app.route('/shutdown')
def shutdown():
    shutdown_cleanup(wheels, camera, stop_event)
    return jsonify({'status': 'ok'})


def main():
    global camera, wheels

    ap = argparse.ArgumentParser(description='Project Follow Server — Godot Simulation')
    ap.add_argument('--port',       type=int, default=5000)
    ap.add_argument('--frame-port', type=int, default=5001)
    ap.add_argument('--wheel-port', type=int, default=5002)
    ap.add_argument('--godot-host', type=str, default='localhost')
    args = ap.parse_args()

    suppress_http_logs()
    print('=' * 60)
    print('PROJECT FOLLOW SERVER — GODOT SIMULATION')
    print('=' * 60)

    print('\n[1/3] Initializing wheels (Godot)...')
    wheels = GodotWheelsDriver(
        WheelPWMConfiguration(pwm_min=0), WheelPWMConfiguration(pwm_min=0),
        godot_host=args.godot_host, godot_port=args.wheel_port,
    )

    print('\n[2/3] Initializing camera (Godot)...')
    camera = GodotCameraDriver(godot_config=GodotCameraConfig(host='0.0.0.0', port=args.frame_port))
    camera.start()

    print('\n[3/3] Starting follower agent...')
    _start_agent()
    print('  agent.main() running')

    web_port = find_available_port(args.port)
    print(f'\nWeb Interface: http://localhost:{web_port}')
    print('=' * 60 + '\n')

    try:
        app.run(host='127.0.0.1', port=web_port, debug=False, threaded=True)
    except KeyboardInterrupt:
        print('\nShutting down...')
    finally:
        shutdown_cleanup(wheels, camera, stop_event)


if __name__ == '__main__':
    sys.exit(main())
