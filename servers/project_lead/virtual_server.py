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

import tasks.project_lead.packages.agent as agent

HTML_TEMPLATE = get_template('lead', 'Convoy — Lead Bot', 'Godot Simulation')

app        = Flask(__name__)
camera     = None
wheels     = None
stop_event = threading.Event()

_agent_thread = None
_agent_lock   = threading.Lock()

_frame_queue     = LatestFrame()
_debug_info_lock = threading.Lock()
_debug_info      = {}

_MAP_DATA  = load_map_for_scene(GODOT_SCENES.get('project_lead', ''))
_CONFIG_FILE = os.path.normpath(os.path.join(script_dir, '..', '..', 'config', 'project_lead_config.yaml'))

# Optional 2x2 debug montage: annotated camera + yellow / white / red masks,
# same panels as the real lead server, so HSV tuning carries over to the sim.
_SHOW_MASKS = True
_PANEL_SIZE = (320, 240)
_STREAM_QUALITY = 60
_ROUTE = []
_ROUTE_MODE = 'fixed'
_STOP_IDS = set()
_SLOW_IDS = set()
try:
    import yaml as _yaml
    from tasks.visual_lane_servoing.packages.visual_servoing_activity import detect_lane_markings
    from tasks.project.packages.red_line import RedLineDetector
    _red_detector = RedLineDetector()
    try:
        with open(_CONFIG_FILE) as _cf:
            _lcfg = _yaml.safe_load(_cf) or {}
        _ROUTE      = [str(s).lower() for s in (_lcfg.get('route') or [])]
        _ROUTE_MODE = str(_lcfg.get('route_mode', 'fixed')).lower()
        _STOP_IDS = {int(i) for i in (_lcfg.get('apriltag_stop_ids') or [])}
        _SLOW_IDS = {int(i) for i in (_lcfg.get('apriltag_slow_ids') or [])}
    except Exception:
        pass
    _MASKS_AVAILABLE = True
except Exception as _mask_err:
    print(f'[lead sim] mask preview disabled: {_mask_err}')
    _MASKS_AVAILABLE = False


def _mask_to_bgr(mask, color, label):
    out = np.zeros((mask.shape[0], mask.shape[1], 3), dtype=np.uint8)
    out[mask > 0] = color
    cv2.putText(out, label, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return out


def _montage(frame_bgr):
    panel = cv2.resize(frame_bgr, _PANEL_SIZE, interpolation=cv2.INTER_AREA)
    cam = visualize(panel)
    z = np.zeros((_PANEL_SIZE[1], _PANEL_SIZE[0]), dtype=np.uint8)
    try:
        m_yellow, m_white = detect_lane_markings(panel)
    except Exception:
        m_yellow = m_white = z
    try:
        m_red = (_red_detector._red_mask(panel) > 0).astype(np.uint8)
    except Exception:
        m_red = z
    yellow = _mask_to_bgr(m_yellow, (0, 255, 255), 'YELLOW centerline')
    white  = _mask_to_bgr(m_white,  (255, 255, 255), 'WHITE edge')
    red    = _mask_to_bgr(m_red,    (0, 0, 255),     'RED stop-line')
    return cv2.vconcat([cv2.hconcat([cam, yellow]), cv2.hconcat([white, red])])


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
    cv2.putText(display, f"Route: {info.get('route_idx', 0)}", (10, 90), font, 0.5, (0, 255, 0), 1)

    rl = info.get('red_line')
    if rl:
        cv2.putText(display, f"RedLine: w={rl[0]} d={rl[1]}", (10, h - 60), font, 0.5, (0, 0, 255), 1)
    tag_ids = info.get('apriltag_ids', [])
    if tag_ids:
        parts = []
        for t in tag_ids:
            kind = 'STOP' if t in _STOP_IDS else ('SLOW' if t in _SLOW_IDS else '?')
            parts.append(f'{t}={kind}')
        cv2.putText(display, 'TAGS: ' + '  '.join(parts), (10, h - 38), font, 0.55, (0, 255, 255), 2)
    cv2.putText(display, f"{info.get('fps', 0.0):.1f} FPS", (w - 100, 25), font, 0.5, (200, 200, 200), 1)
    return display


# While the agent runs, it is the frame pump; when paused nothing pumps and the
# stream froze on the last frame — pull straight from the camera instead.
# Locked: several /video clients run this generator concurrently.
_direct_read_lock = threading.Lock()


def _refresh_frame_if_paused():
    if _agent_alive() or camera is None:
        return
    try:
        with _direct_read_lock:
            ok, live = camera.read()
        if ok and live is not None:
            _frame_queue.put_nowait(live.copy())   # /sample stays fresh too
    except Exception:
        pass


def generate_frames():
    while True:
        _refresh_frame_if_paused()
        frame = _frame_queue.get_latest()
        try:
            if frame is None:
                display = visualize(None)
            elif _SHOW_MASKS and _MASKS_AVAILABLE:
                display = _montage(frame)
            else:
                display = visualize(frame)
            if frame is not None and not _agent_alive():
                _ov = 'MANUAL CONTROL' if _manual_mode else 'AGENT PAUSED'
                cv2.putText(display, _ov, (10, display.shape[0] - 12),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
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
    """Start the lead agent thread. Sim has no LED hardware and no wheel
    encoders; the agent handles leds=None (LED commands no-op) and
    encoders=None (turns fall back to lane-reacquire + timeout)."""
    global _agent_thread
    with _agent_lock:
        if _agent_alive():
            return False
        stop_event.clear()
        _agent_thread = threading.Thread(
            target=agent.main,
            args=(camera, wheels, None, stop_event, _frame_queue, _debug_info_lock, _debug_info),
            kwargs={'encoders': None},
            daemon=True,
            name='LeadAgentThread',
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


# --- manual drive ------------------------------------------------------------
# Manual mode stops the autonomous agent and drives the wheels straight from the
# UI. A deadman watchdog zeroes the wheels if /drive stops arriving (closed tab,
# stuck key) so the bot can never coast away on a stale command.
_manual_mode      = False
_last_manual_cmd  = 0.0
_manual_watchdog  = None
_MANUAL_TIMEOUT   = 0.6     # seconds of silence before the deadman cuts power
_MANUAL_MAX       = 0.45    # forward/back wheel magnitude cap [-1, 1]
_MANUAL_TURN      = 0.35    # turn differential added to each wheel


def _zero_wheels():
    if wheels is not None:
        try:
            wheels.set_wheels_speed(0.0, 0.0)
        except Exception:
            pass


def _manual_watchdog_loop():
    # Runs for the life of the process (daemon). NOTE: can't gate on stop_event
    # here — entering manual mode sets it (to halt the agent), which would kill
    # the watchdog exactly when it's needed. Gate on _manual_mode instead.
    while True:
        if _manual_mode and (time.time() - _last_manual_cmd) > _MANUAL_TIMEOUT:
            _zero_wheels()
        time.sleep(0.15)


def _ensure_watchdog():
    global _manual_watchdog
    if _manual_watchdog is None or not _manual_watchdog.is_alive():
        _manual_watchdog = threading.Thread(target=_manual_watchdog_loop,
                                             daemon=True, name='ManualWatchdog')
        _manual_watchdog.start()


# --- routes ------------------------------------------------------------------

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, map_json=json.dumps(_MAP_DATA))


@app.route('/video')
def video():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/status')
def status():
    running = _agent_alive()
    if wheels is not None and not running:
        # The agent's send_wheels normally drains the Godot socket; while it
        # is paused, poll here so pose/game state stay fresh.
        wheels.is_game_over()
    pose = None
    game = {}
    if wheels is not None:
        gs = wheels.game_state
        if gs.pose_x is not None:
            pose = {'x': gs.pose_x, 'z': gs.pose_z, 'theta': gs.heading_rad}
        game = {
            'game_over': gs.game_over,
            'survival_time': round(gs.survival_time, 1),
            'total_distance': round(gs.distance_traveled, 2),
            'collision_duck': gs.collision_duck,
        }
    with _debug_info_lock:
        info = dict(_debug_info)
    return jsonify({
        'agent': info,
        'agent_running': running,
        'manual_mode': _manual_mode,
        'pose': pose,
        'game': game,
        'route': _ROUTE,
        'route_mode': _ROUTE_MODE,
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


@app.route('/mode', methods=['GET', 'POST'])
def mode():
    """Toggle manual/autonomous drive. Manual stops the agent and hands the
    wheels to /drive; autonomous restarts the agent."""
    global _manual_mode, _last_manual_cmd
    if request.method == 'GET':
        return jsonify(manual=_manual_mode, agent_running=_agent_alive())
    body = request.get_json(silent=True) or {}
    want_manual = bool(body.get('manual'))
    if want_manual == _manual_mode:
        return jsonify(status='ok', manual=_manual_mode, message='no change')
    if want_manual:
        _stop_agent()                 # autonomy off; manual owns the wheels
        _manual_mode = True
        _last_manual_cmd = time.time()
        _zero_wheels()
        _ensure_watchdog()
        return jsonify(status='ok', manual=True, message='manual control')
    _manual_mode = False
    _zero_wheels()
    _start_agent()
    return jsonify(status='ok', manual=False, message='autonomous')


@app.route('/drive', methods=['POST'])
def drive():
    """Manual wheel command: {linear, angular} each in [-1, 1], mixed to L/R."""
    global _last_manual_cmd
    if not _manual_mode:
        return jsonify(status='error', message='not in manual mode')
    body = request.get_json(silent=True) or {}
    try:
        lin = max(-1.0, min(1.0, float(body.get('linear', 0.0))))
        ang = max(-1.0, min(1.0, float(body.get('angular', 0.0))))
    except (TypeError, ValueError):
        return jsonify(status='error', message='bad drive values')
    left  = max(-1.0, min(1.0, lin * _MANUAL_MAX + ang * _MANUAL_TURN))
    right = max(-1.0, min(1.0, lin * _MANUAL_MAX - ang * _MANUAL_TURN))
    if wheels is not None:
        try:
            wheels.set_wheels_speed(left, right)
        except Exception as e:
            return jsonify(status='error', message=f'drive failed: {e}')
    _last_manual_cmd = time.time()
    return jsonify(status='ok', left=round(left, 3), right=round(right, 3))


@app.route('/agent/start', methods=['POST'])
def agent_start():
    if _manual_mode:
        return jsonify(status='error', message='in manual mode — switch to autonomous first')
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
    """Respawn the robot in Godot and restart the agent with a fresh FSM."""
    _, stopped = _stop_agent()
    if not stopped:
        return jsonify(status='error', message='agent is busy stopping; try again in a few seconds')
    if wheels is not None:
        wheels.reset_game()
    with _debug_info_lock:
        _debug_info.clear()
    time.sleep(0.2)
    if wheels is not None:
        # Drop state pushed before the respawn so /status can't serve a
        # pre-reset pose or a stale game_over.
        wheels.clear_state()
    if _manual_mode:
        _zero_wheels()                # respawned; stay under manual control
        return jsonify(status='ok', message='simulation reset (manual)')
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

    ap = argparse.ArgumentParser(description='Project Lead Server — Godot Simulation')
    ap.add_argument('--port',       type=int, default=5000)
    ap.add_argument('--frame-port', type=int, default=5001)
    ap.add_argument('--wheel-port', type=int, default=5002)
    ap.add_argument('--godot-host', type=str, default='localhost')
    args = ap.parse_args()

    suppress_http_logs()
    print('=' * 60)
    print('PROJECT LEAD SERVER — GODOT SIMULATION')
    print('=' * 60)

    print('\n[1/3] Initializing wheels (Godot)...')
    wheels = GodotWheelsDriver(
        WheelPWMConfiguration(pwm_min=0), WheelPWMConfiguration(pwm_min=0),
        godot_host=args.godot_host, godot_port=args.wheel_port,
    )

    print('\n[2/3] Initializing camera (Godot)...')
    camera = GodotCameraDriver(godot_config=GodotCameraConfig(host='0.0.0.0', port=args.frame_port))
    camera.start()

    print('\n[3/3] Starting lead agent...')
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
