import sys
import os
import json
import signal
import threading
import argparse

script_dir   = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.join(script_dir, '..', '..')
sys.path.insert(0, project_root)

from flask import Flask, Response, jsonify, render_template_string, request
import numpy as np
import cv2

from duckiebot.camera_driver import CameraDriver
from duckiebot.wheel_driver import DaguWheelsDriver
from duckiebot.wheel_driver.wheels_driver_abs import WheelPWMConfiguration
from duckiebot.led_driver import LEDDriver
from launcher.ports import find_available_port
from servers.common import (handle_hsv_tuning, shutdown_cleanup, suppress_http_logs,
                            update_yaml_values)

import tasks.project_lead.packages.agent as agent

# Same rich UI as the sim server; falls back to the legacy plain page when the
# bot's tree predates servers/templates (older deploys).
try:
    from servers.templates.convoy import get_template
    HTML_TEMPLATE = get_template('lead', 'Convoy — Lead Bot', 'Real Robot', sim=False)
except Exception as _tpl_err:
    print(f'[lead] convoy template unavailable ({_tpl_err}) — using the legacy page')
    HTML_TEMPLATE = None


def _load_map_data():
    """Track map for the UI: parse the Godot scene when developing locally,
    else the baked copy shipped in config/ (the physical KIU track is the
    same layout). Regenerate config/kiu_map.json after editing the scene."""
    try:
        from launcher.config import GODOT_SCENES
        from servers.sim_map import load_map_for_scene
        m = load_map_for_scene(GODOT_SCENES.get('project_lead', ''))
        if m:
            return m
    except Exception:
        pass
    try:
        with open(os.path.join(project_root, 'config', 'kiu_map.json')) as f:
            return json.load(f)
    except Exception:
        return None


_MAP_DATA = _load_map_data()

INDEX_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Convoy — Lead</title>
<style>
  body { margin:0; background:#111; color:#eee; font-family:sans-serif; }
  header { padding:12px 16px; background:#1a1a1a; border-bottom:1px solid #333; }
  header .hint { color:#888; font-size:13px; font-weight:normal; }
  main { display:flex; justify-content:center; padding:16px; }
  img.stream { max-width:100%; height:auto; border:1px solid #333; background:#000; cursor:crosshair; }
  #hsv { text-align:center; font-family:monospace; font-size:18px; padding:6px; min-height:22px; }
  #log { max-width:640px; margin:0 auto; font-family:monospace; font-size:12px; color:#9bd; }
</style></head>
<body>
  <header>Convoy — Lead Bot &nbsp;<span class="hint">click the line in the top-left camera panel to read its H/S/V</span></header>
  <main><img id="cam" class="stream" src="/video" alt="camera"></main>
  <div id="hsv"></div>
  <div id="log"></div>
<script>
  const img = document.getElementById('cam');
  const out = document.getElementById('hsv');
  const log = document.getElementById('log');
  img.addEventListener('click', async (e) => {
    const r = img.getBoundingClientRect();
    const fx = (e.clientX - r.left) / r.width;
    const fy = (e.clientY - r.top) / r.height;
    try {
      const resp = await fetch('/sample?fx=' + fx.toFixed(4) + '&fy=' + fy.toFixed(4));
      const d = await resp.json();
      if (d.hint)  { out.style.color = '#fc8'; out.textContent = d.hint; return; }
      if (d.error) { out.style.color = '#f88'; out.textContent = d.error; return; }
      out.style.color = '#8f8';
      out.textContent = 'H ' + d.h + '   S ' + d.s + '   V ' + d.v + '   (pixel ' + d.px + ',' + d.py + ')';
      const line = document.createElement('div');
      line.textContent = 'H=' + d.h + ' S=' + d.s + ' V=' + d.v;
      log.prepend(line);
    } catch (err) { out.style.color = '#f88'; out.textContent = 'sample failed'; }
  });
</script>
</body></html>
"""

app        = Flask(__name__)
camera     = None
wheels     = None
leds       = None
encoders   = None
stop_event = threading.Event()

_debug_info_lock = threading.Lock()
_debug_info = {}


class _LatestFrame:
    """Single-slot frame holder: the newest frame always wins, never blocks and
    never 'fills up'. Replaces the Queue so the stream shows the freshest frame
    the agent produced and can't get wedged on an empty queue."""
    def __init__(self):
        self._lock = threading.Lock()
        self._frame = None

    def put_nowait(self, frame):   # Queue-compatible name: the agent calls this
        with self._lock:
            self._frame = frame

    def get_latest(self):
        with self._lock:
            return self._frame


# No queue: the agent overwrites the single latest frame; the stream reads it.
_frame_queue = _LatestFrame()

# Display stream: downscale + lower JPEG quality so frames are ~4x smaller over
# wifi (cuts latency), and always send the freshest frame (drain the queue).
_STREAM_SIZE = (480, 360)   # (width, height) of the streamed image
_STREAM_QUALITY = 45        # JPEG quality [0..100] for the stream

# Optional 2x2 debug montage: annotated camera + yellow / white / red masks, so
# HSV tuning can be done from the browser. Each panel is _PANEL_SIZE, giving a
# 640x480 montage. Falls back to the plain feed if the vision modules can't be
# imported. NOTE: masks reflect the HSV config loaded at startup — redeploy to
# pick up edits to lane_servoing_hsv_config.yaml.
_SHOW_MASKS = True
_PANEL_SIZE = (320, 240)    # (w, h) per panel
# STOP/SLOW tag IDs (overlay labels) + route shown by the UI, from config.
_CONFIG_FILE = os.path.normpath(os.path.join(project_root, "config", "project_lead_config.yaml"))
_STOP_IDS = set()
_SLOW_IDS = set()
_ROUTE = []
_ROUTE_MODE = 'fixed'
try:
    import yaml as _yaml
    with open(_CONFIG_FILE) as _cf:
        _lcfg = _yaml.safe_load(_cf) or {}
    _STOP_IDS = {int(i) for i in (_lcfg.get("apriltag_stop_ids") or [])}
    _SLOW_IDS = {int(i) for i in (_lcfg.get("apriltag_slow_ids") or [])}
    _ROUTE = [str(s).lower() for s in (_lcfg.get("route") or [])]
    # route_mode 'auto' needs a live pose; the real robot has none and the
    # agent falls back to the fixed list — show what actually runs.
    _ROUTE_MODE = 'fixed'
except Exception as _cfg_err:
    print(f"[lead] config read failed: {_cfg_err}")
try:
    from tasks.visual_lane_servoing.packages.visual_servoing_activity import detect_lane_markings
    from tasks.project.packages.red_line import RedLineDetector
    _red_detector = RedLineDetector()
    _MASKS_AVAILABLE = True
except Exception as _mask_err:
    print(f"[lead] mask preview disabled: {_mask_err}")
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
    yellow = _mask_to_bgr(m_yellow, (0, 255, 255), "YELLOW centerline")
    white  = _mask_to_bgr(m_white,  (255, 255, 255), "WHITE edge")
    red    = _mask_to_bgr(m_red,    (0, 0, 255),     "RED stop-line")
    top    = cv2.hconcat([cam, yellow])
    bottom = cv2.hconcat([white, red])
    return cv2.vconcat([top, bottom])


def visualize(frame_bgr):
    if frame_bgr is None:
        blank = np.zeros((480, 640, 3), dtype=np.uint8)
        cv2.putText(blank, "Waiting for camera...", (160, 240),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (80, 80, 80), 2)
        return blank

    display = frame_bgr.copy()
    h, w = display.shape[:2]
    with _debug_info_lock:
        info = _debug_info.copy()
    font = cv2.FONT_HERSHEY_SIMPLEX

    state = info.get('state', 'INIT')
    cv2.putText(display, f"State: {state}", (10, 25), font, 0.6, (0, 255, 0), 2)
    cv2.putText(display, f"Base: {info.get('base_speed', 0.0):.2f} "
                         f"Steer: {info.get('steering', 0.0):+.2f}", (10, 50), font, 0.5, (0, 255, 0), 1)
    cv2.putText(display, f"L: {info.get('left_speed', 0.0):+.2f}  "
                         f"R: {info.get('right_speed', 0.0):+.2f}", (10, 70), font, 0.5, (0, 255, 0), 1)
    cv2.putText(display, f"Route: {info.get('route_idx', 0)}", (10, 90), font, 0.5, (0, 255, 0), 1)

    rl = info.get('red_line')
    if rl:
        cv2.putText(display, f"RedLine: w={rl[0]} d={rl[1]}", (10, h - 60),
                    font, 0.5, (0, 0, 255), 1)
    tag_ids = info.get('apriltag_ids', [])
    if tag_ids:
        parts = []
        for t in tag_ids:
            kind = "STOP" if t in _STOP_IDS else ("SLOW" if t in _SLOW_IDS else "?")
            parts.append(f"{t}={kind}")
        cv2.putText(display, "TAGS: " + "  ".join(parts), (10, h - 38), font, 0.55, (0, 255, 255), 2)
    else:
        cv2.putText(display, "TAGS: none", (10, h - 38), font, 0.5, (120, 120, 120), 1)
    cv2.putText(display, f"{info.get('fps', 0.0):.1f} FPS", (w - 100, 25), font, 0.5, (200, 200, 200), 1)
    return display


# While the agent runs, it is the frame pump (it queues every frame it reads).
# When it is paused/stopped nothing pumps, so the stream froze on the last
# frame: pull straight from the camera instead. Locked because several /video
# clients run this generator concurrently and cv2 capture isn't thread-safe.
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
    import time
    while True:
        _refresh_frame_if_paused()
        frame = _frame_queue.get_latest()
        if frame is None:
            blank = np.zeros((_STREAM_SIZE[1], _STREAM_SIZE[0], 3), dtype=np.uint8)
            cv2.putText(blank, "Waiting for camera...", (90, _STREAM_SIZE[1] // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (80, 80, 80), 2)
            ret, jpeg = cv2.imencode('.jpg', blank, [cv2.IMWRITE_JPEG_QUALITY, _STREAM_QUALITY])
            if ret:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
            time.sleep(0.1)
            continue
        try:
            if _SHOW_MASKS and _MASKS_AVAILABLE:
                display = _montage(frame)
            else:
                display = visualize(cv2.resize(frame, _STREAM_SIZE, interpolation=cv2.INTER_AREA))
            if not _agent_alive():   # stale State/FPS text underneath otherwise
                cv2.putText(display, 'AGENT PAUSED', (10, display.shape[0] - 12),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
            ret, jpeg = cv2.imencode('.jpg', display, [cv2.IMWRITE_JPEG_QUALITY, _STREAM_QUALITY])
            if ret:
                yield (b'--frame\r\n'
                       b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')
        except Exception as e:
            print(f'[VideoStream] render error: {e}')
        time.sleep(0.03)   # ~30 fps cap so the stream loop doesn't busy-spin


# --- agent lifecycle ----------------------------------------------------------

_agent_thread = None
_agent_lock   = threading.Lock()


def _agent_alive():
    return _agent_thread is not None and _agent_thread.is_alive()


def _start_agent():
    global _agent_thread
    with _agent_lock:
        if _agent_alive():
            return False
        stop_event.clear()
        _agent_thread = threading.Thread(
            target=agent.main,
            args=(camera, wheels, leds, stop_event, _frame_queue, _debug_info_lock, _debug_info),
            kwargs={'encoders': encoders},
            daemon=True,
            name='LeadAgentThread',
        )
        _agent_thread.start()
        return True


def _stop_agent(timeout=3.0):
    """Returns (was_running, stopped_now)."""
    with _agent_lock:
        if not _agent_alive():
            return False, True
        stop_event.set()
        _agent_thread.join(timeout)
        try:
            wheels.set_wheels_speed(0.0, 0.0)   # real hardware: never coast on pause
        except Exception:
            pass
        return True, not _agent_thread.is_alive()


# --- routes --------------------------------------------------------------------

@app.route('/')
def index():
    if HTML_TEMPLATE is None:
        return INDEX_HTML
    return render_template_string(HTML_TEMPLATE, map_json=json.dumps(_MAP_DATA))


@app.route('/video')
def video():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/status')
def status():
    # Same shape as the sim server so the shared UI works unchanged; the real
    # robot has no localization (pose null) and no game state (empty -> hidden).
    with _debug_info_lock:
        info = dict(_debug_info)
    return jsonify({
        'agent': info,
        'agent_running': _agent_alive(),
        'pose': None,
        'game': {},
        'route': _ROUTE,
        'route_mode': _ROUTE_MODE,
    })


@app.route('/sample')
def sample():
    """Click-to-sample HSV for tuning. fx/fy are click fractions [0,1] over the
    streamed image. With the 2x2 montage the camera is the top-left quarter;
    otherwise the whole frame. Returns the median H/S/V of a small patch in the
    same BGR->HSV space the lane/red detectors use -- paste straight into the
    HSV config."""
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
            return jsonify(hint='click the line in the TOP-LEFT camera panel')
        sx, sy = fx * 2.0, fy * 2.0           # quarter -> full-frame fraction
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


_LANE_CONFIG_FILE = os.path.normpath(os.path.join(project_root, 'config', 'lane_servoing_config.yaml'))
_HSV_CONFIG_FILE  = os.path.normpath(os.path.join(project_root, 'config', 'lane_servoing_hsv_config.yaml'))


@app.route('/hsv', methods=['GET', 'POST'])
def hsv_tuning():
    return jsonify(handle_hsv_tuning(request, _HSV_CONFIG_FILE))

# UI tuning bounds: (min, max) — same as the sim server.
_TUNE_BOUNDS = {'speed': (0.05, 0.6), 'kp': (0.0, 1.0), 'kd': (0.0, 2.0)}


@app.route('/tuning', methods=['GET', 'POST'])
def tuning():
    """Live-tune cruise speed and the lane PD gains. Applies to the running
    agent immediately and persists to the deployed YAMLs (a redeploy from the
    dev machine overwrites them — copy values back into the repo to keep)."""
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
        return jsonify(status='error', message='agent is still stopping — wheels are zeroed')
    return jsonify(status='ok', message='agent paused')


@app.route('/reset', methods=['POST'])
def reset():
    """No sim to respawn: restart the agent with a fresh FSM/route. Place the
    robot back on the track by hand first."""
    _, stopped = _stop_agent()
    if not stopped:
        return jsonify(status='error', message='agent is busy stopping; try again in a few seconds')
    with _debug_info_lock:
        _debug_info.clear()
    _start_agent()
    return jsonify(status='ok', message='agent restarted (fresh route)')


@app.route('/command', methods=['POST'])
def command():
    return jsonify({'status': 'ok'})


@app.route('/shutdown')
def shutdown():
    shutdown_cleanup(wheels, camera, stop_event)
    return jsonify({'status': 'ok'})


def main():
    global camera, wheels, leds, encoders, stop_event

    ap = argparse.ArgumentParser(description='Project Lead Server — Real Hardware')
    ap.add_argument('--port', type=int, default=5000)
    args = ap.parse_args()

    suppress_http_logs()
    print('=' * 60)
    print('PROJECT LEAD SERVER — REAL HARDWARE')
    print('=' * 60)

    print('\n[1/5] Initializing LED driver...')
    try:
        leds = LEDDriver()
        leds.all_off()
        print('  LEDs: ok')
    except Exception as e:
        print(f'  LEDs: not available ({e})')
        leds = None

    print('\n[2/5] Initializing wheels driver...')
    wheels = DaguWheelsDriver(WheelPWMConfiguration(), WheelPWMConfiguration())
    print('  Wheels: ok')

    print('\n[3/5] Initializing wheel encoders (optional)...')
    try:
        from duckiebot.encoder_driver.encoder_driver import WheelEncoderPair
        encoders = WheelEncoderPair()
        print('  Encoders: ok')
    except Exception as e:
        print(f'  Encoders: not available ({e}); turns fall back to lane-reacquire + timeout')
        encoders = None

    print('\n[4/5] Initializing camera driver...')
    camera = CameraDriver()
    camera.start()
    print('  Camera: ok')

    print('\n[5/5] Starting lead agent...')
    _start_agent()
    print('  agent.main() running')

    def _shutdown(signum, frame):
        print('\nShutting down...')
        if leds:
            try:
                leds.all_off(); leds.release()
            except Exception:
                pass
        if encoders:
            try:
                encoders.shutdown()
            except Exception:
                pass
        shutdown_cleanup(wheels, camera, stop_event)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT,  _shutdown)

    web_port = find_available_port(args.port)
    print(f'\nWeb Interface: http://localhost:{web_port}')
    print('Press Ctrl+C to stop\n')

    try:
        app.run(host='0.0.0.0', port=web_port, debug=False, threaded=True)
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        if leds:
            try:
                leds.all_off(); leds.release()
            except Exception:
                pass
        if encoders:
            try:
                encoders.shutdown()
            except Exception:
                pass
        shutdown_cleanup(wheels, camera, stop_event)


if __name__ == '__main__':
    sys.exit(main())
