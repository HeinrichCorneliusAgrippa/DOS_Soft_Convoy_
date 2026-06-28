import sys
import os
import signal
import threading
import argparse
import queue

script_dir   = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.join(script_dir, '..', '..')
sys.path.insert(0, project_root)

from flask import Flask, Response, jsonify, request
import numpy as np
import cv2

from duckiebot.camera_driver import CameraDriver
from duckiebot.wheel_driver import DaguWheelsDriver
from duckiebot.wheel_driver.wheels_driver_abs import WheelPWMConfiguration
from duckiebot.led_driver import LEDDriver
from launcher.ports import find_available_port
from servers.common import (handle_hsv_tuning, shutdown_cleanup,
                            suppress_http_logs, update_yaml_values)

import tasks.project_follow.packages.agent as agent

INDEX_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Convoy — Follower</title>
<style>
  body { margin:0; background:#111; color:#eee; font-family:sans-serif; }
  header { padding:12px 16px; background:#1a1a1a; border-bottom:1px solid #333; }
  header .hint { color:#888; font-size:13px; font-weight:normal; }
  main { display:flex; flex-wrap:wrap; align-items:flex-start; justify-content:center; gap:16px; padding:16px; }
  .cam-col { flex:1 1 420px; max-width:640px; min-width:300px; }
  .side-col { flex:1 1 360px; max-width:520px; min-width:300px; }
  img.stream { display:block; width:100%; height:auto; border:1px solid #333; background:#000; cursor:crosshair; }
  #hsv { text-align:center; font-family:monospace; font-size:16px; padding:6px; min-height:22px; }
  #log { margin:8px 0 0; font-family:monospace; font-size:12px; color:#9bd; }
  #controls { text-align:center; padding:0 0 8px; }
  #controls button { font-size:14px; padding:8px 18px; margin:0 6px; border:none; border-radius:4px; cursor:pointer; color:#fff; }
  #controls button:disabled { opacity:0.4; cursor:not-allowed; }
  #btnStop  { background:#a33; }
  #btnStart { background:#3a3; }
  #agentMsg { text-align:center; font-family:monospace; font-size:13px; min-height:18px; color:#ccc; margin-bottom:8px; }
  #speedPanel { margin:0 0 12px; padding:10px 14px; background:#1a1a1a; border:1px solid #333; border-radius:6px; }
  #speedPanel h3 { margin:0 0 4px; font-size:14px; }
  #speedPanel .hint { color:#888; font-size:12px; margin-bottom:8px; }
  .speed-row { display:flex; align-items:center; gap:10px; font-family:monospace; font-size:13px; }
  .speed-row input[type=range] { flex:1; }
  .speed-row .v { width:46px; text-align:right; color:#8f8; font-size:15px; }
  #speedMsg { font-family:monospace; font-size:12px; min-height:16px; color:#8f8; margin-top:6px; }
  #hsvPanel { margin:0 0 12px; padding:10px 14px; background:#1a1a1a; border:1px solid #333; border-radius:6px; }
  #hsvPanel h3 { margin:0 0 4px; font-size:14px; }
  #hsvPanel .hint { color:#888; font-size:12px; margin-bottom:8px; }
  .hsv-group { margin-bottom:8px; }
  .hsv-group .gl { font-size:12px; color:#bbb; margin:4px 0; font-weight:bold; }
  .hsv-row { display:flex; align-items:center; gap:8px; font-family:monospace; font-size:12px; }
  .hsv-row label { width:62px; color:#aaa; }
  .hsv-row input[type=range] { flex:1; }
  .hsv-row .v { width:34px; text-align:right; color:#8f8; }
  #hsvMsg { font-family:monospace; font-size:12px; min-height:16px; color:#8f8; margin-top:6px; }
</style></head>
<body>
  <header>Convoy — Follower Bot &nbsp;<span class="hint">click the line in the top-left camera panel to read its H/S/V</span></header>
  <main>
    <div class="cam-col">
      <img id="cam" class="stream" src="/video" alt="camera">
      <div id="hsv"></div>
    </div>
    <div class="side-col">
      <div id="controls">
        <button id="btnStop">Stop Agent</button>
        <button id="btnStart">Start Agent</button>
      </div>
      <div id="agentMsg"></div>
      <div id="speedPanel">
        <h3>Cruise speed <span style="color:#8f8;font-size:11px;">(live)</span></h3>
        <div class="hint">Sets the follower's cruise speed. Applies to the running FSM instantly and saves to the config. State machine still tapers toward 0 as it nears the leader and uses its own pursuit/turn speeds.</div>
        <div class="speed-row">
          <input type="range" id="speedRange" min="0.05" max="0.6" step="0.01" value="0.3">
          <span class="v" id="speedVal">0.30</span>
        </div>
        <div id="speedMsg"></div>
      </div>
      <div id="hsvPanel">
        <h3>Lane HSV tuning <span style="color:#8f8;font-size:11px;">(live)</span></h3>
        <div class="hint">Sliders apply to the running yellow/white lane detector instantly &mdash; watch the mask panels &mdash; and save to the config. Click the camera panel to sample a pixel's H/S/V, then bracket the bounds around it.</div>
        <div id="hsvSliders"></div>
        <div id="hsvMsg"></div>
      </div>
      <div id="log"></div>
    </div>
  </main>
<script>
  const img = document.getElementById('cam');
  const out = document.getElementById('hsv');
  const log = document.getElementById('log');
  const btnStop = document.getElementById('btnStop');
  const btnStart = document.getElementById('btnStart');
  const agentMsg = document.getElementById('agentMsg');

  async function postAgent(action) {
    btnStop.disabled = true; btnStart.disabled = true;
    try {
      const resp = await fetch('/agent/' + action, { method: 'POST' });
      const d = await resp.json();
      agentMsg.style.color = (d.status === 'error') ? '#f88' : '#8f8';
      agentMsg.textContent = d.message || '';
    } catch (err) {
      agentMsg.style.color = '#f88';
      agentMsg.textContent = 'request failed';
    }
    pollStatus();
  }
  btnStop.addEventListener('click', () => postAgent('stop'));
  btnStart.addEventListener('click', () => postAgent('start'));

  async function pollStatus() {
    try {
      const resp = await fetch('/status');
      const d = await resp.json();
      const running = !!d.agent_running;
      btnStop.disabled = !running;
      btnStart.disabled = running;
    } catch (err) { /* keep last known button state */ }
  }
  pollStatus();
  setInterval(pollStatus, 2000);

  // --- live cruise speed ---
  const speedRange = document.getElementById('speedRange');
  const speedVal = document.getElementById('speedVal');
  const speedMsg = document.getElementById('speedMsg');
  let speedTimer = null;
  speedRange.addEventListener('input', () => {
    speedVal.textContent = (+speedRange.value).toFixed(2);
    clearTimeout(speedTimer);
    speedTimer = setTimeout(applySpeed, 250);
  });
  async function loadSpeed() {
    try {
      const d = await (await fetch('/speed')).json();
      if (d.bounds) { speedRange.min = d.bounds[0]; speedRange.max = d.bounds[1]; }
      if (d.speed !== null && d.speed !== undefined) {
        speedRange.value = d.speed;
        speedVal.textContent = (+d.speed).toFixed(2);
      }
    } catch (err) { /* /speed may be unavailable */ }
  }
  async function applySpeed() {
    try {
      const resp = await fetch('/speed', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ speed: +speedRange.value }),
      });
      const d = await resp.json();
      speedMsg.style.color = (d.status === 'error') ? '#f88' : '#8f8';
      speedMsg.textContent = d.message || '';
      if (d.speed !== null && d.speed !== undefined) {
        speedRange.value = d.speed;
        speedVal.textContent = (+d.speed).toFixed(2);
      }
    } catch (err) { speedMsg.style.color = '#f88'; speedMsg.textContent = 'apply failed'; }
  }
  loadSpeed();

  // --- live lane HSV tuning ---
  const HSV_KNOBS = [
    ['yellow', 'Yellow', [
      ['yellow_lower_h', 'H min', 179], ['yellow_upper_h', 'H max', 179],
      ['yellow_lower_s', 'S min', 255], ['yellow_upper_s', 'S max', 255],
      ['yellow_lower_v', 'V min', 255], ['yellow_upper_v', 'V max', 255],
    ]],
    ['white', 'White', [
      ['white_lower_h', 'H min', 179], ['white_upper_h', 'H max', 179],
      ['white_lower_s', 'S min', 255], ['white_upper_s', 'S max', 255],
      ['white_lower_v', 'V min', 255], ['white_upper_v', 'V max', 255],
    ]],
  ];
  const hsvMsg = document.getElementById('hsvMsg');
  const hsvKeys = [];
  let hsvTimer = null;
  (function buildHsv() {
    const root = document.getElementById('hsvSliders');
    for (const [, group, knobs] of HSV_KNOBS) {
      const g = document.createElement('div');
      g.className = 'hsv-group';
      g.innerHTML = '<div class="gl">' + group + '</div>';
      for (const [key, label, hi] of knobs) {
        hsvKeys.push(key);
        const row = document.createElement('div');
        row.className = 'hsv-row';
        row.innerHTML = '<label>' + label + '</label>' +
          '<input type="range" min="0" max="' + hi + '" step="1" id="r-' + key + '">' +
          '<span class="v" id="v-' + key + '">0</span>';
        g.appendChild(row);
      }
      root.appendChild(g);
    }
    for (const key of hsvKeys) {
      const el = document.getElementById('r-' + key);
      el.addEventListener('input', () => {
        document.getElementById('v-' + key).textContent = el.value;
        clearTimeout(hsvTimer);
        hsvTimer = setTimeout(applyHsv, 300);
      });
    }
  })();
  async function loadHsv() {
    try {
      const d = await (await fetch('/hsv')).json();
      for (const key of hsvKeys) {
        if (d[key] === undefined) continue;
        document.getElementById('r-' + key).value = d[key];
        document.getElementById('v-' + key).textContent = d[key];
      }
    } catch (err) { /* /hsv may be unavailable */ }
  }
  async function applyHsv() {
    const body = {};
    for (const key of hsvKeys) body[key] = +document.getElementById('r-' + key).value;
    try {
      const resp = await fetch('/hsv', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      const d = await resp.json();
      hsvMsg.style.color = (d.status === 'error') ? '#f88' : '#8f8';
      hsvMsg.textContent = d.message || '';
    } catch (err) { hsvMsg.style.color = '#f88'; hsvMsg.textContent = 'apply failed'; }
  }
  loadHsv();

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
stop_event = threading.Event()

_agent_thread = None
_agent_lock   = threading.Lock()

_frame_queue = queue.Queue(maxsize=2)
_debug_info_lock = threading.Lock()
_debug_info = {}

# Latest raw camera frame (BGR), kept for the click-to-sample HSV tool.
_latest_frame = None
_latest_frame_lock = threading.Lock()

# While the agent runs, it is the only thing pumping the camera; when it is
# stopped nothing reads frames and the stream would freeze on "Waiting for
# frames...". Pull straight from the camera instead so the live view (and the
# HSV sampler) keep working while the bot is halted. Locked because several
# /video clients run generate_frames() concurrently and the camera must not be
# read from two places at once. This path only runs while the agent is stopped,
# so it never races the agent's own camera.read().
_direct_read_lock = threading.Lock()

# Display stream: downscale + lower JPEG quality so frames are ~4x smaller over
# wifi (cuts latency), and always send the freshest frame (drain the queue).
_STREAM_SIZE = (480, 360)   # (width, height) of the streamed image
_STREAM_QUALITY = 45        # JPEG quality [0..100] for the stream

# Optional 2x2 debug montage: annotated camera + yellow / white lane masks and
# the circle-grid leader detection, so HSV / grid tuning can be done from the
# browser. Each panel is _PANEL_SIZE, giving a 640x480 montage. Falls back to
# the plain feed if the vision modules can't be imported. NOTE: lane masks
# reflect the HSV config loaded at startup — redeploy to pick up edits.
_SHOW_MASKS = True
_PANEL_SIZE = (320, 240)    # (w, h) per panel
try:
    import yaml
    from tasks.visual_lane_servoing.packages.visual_servoing_activity import detect_lane_markings
    from tasks.project.packages.marker_grid import MarkerGridTracker
    _follow_cfg_file = os.path.normpath(os.path.join(
        os.path.dirname(__file__), "..", "..", "config", "project_follow_config.yaml"))
    try:
        with open(_follow_cfg_file) as _cf:
            _follow_cfg = yaml.safe_load(_cf) or {}
    except Exception:
        _follow_cfg = {}
    _grid_tracker = MarkerGridTracker(cfg=_follow_cfg)
    _MASKS_AVAILABLE = True
except Exception as _mask_err:
    print(f"[follow] mask preview disabled: {_mask_err}")
    _MASKS_AVAILABLE = False


def _mask_to_bgr(mask, color, label):
    out = np.zeros((mask.shape[0], mask.shape[1], 3), dtype=np.uint8)
    out[mask > 0] = color
    cv2.putText(out, label, (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return out


def _grid_panel(panel):
    vis = panel.copy()
    try:
        obs = _grid_tracker.update(panel)
        if obs is not None:
            x1, y1, x2, y2 = obs.bbox
            cv2.rectangle(vis, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
            cx, cy = obs.midpoint
            cv2.circle(vis, (int(cx), int(cy)), 4, (0, 0, 255), -1)
    except Exception:
        pass
    cv2.putText(vis, "GRID (leader)", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)
    return vis


def _montage(frame_bgr):
    panel = cv2.resize(frame_bgr, _PANEL_SIZE, interpolation=cv2.INTER_AREA)
    cam = visualize(panel)
    z = np.zeros((_PANEL_SIZE[1], _PANEL_SIZE[0]), dtype=np.uint8)
    try:
        m_yellow, m_white = detect_lane_markings(panel)
    except Exception:
        m_yellow = m_white = z
    yellow = _mask_to_bgr(m_yellow, (0, 255, 255), "YELLOW centerline")
    white  = _mask_to_bgr(m_white,  (255, 255, 255), "WHITE edge")
    grid   = _grid_panel(panel)
    top    = cv2.hconcat([cam, yellow])
    bottom = cv2.hconcat([white, grid])
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

    src = info.get('leader_source')
    if src:
        span = info.get('led_pair_px')
        text = f"Leader: {src}"
        if span:
            text += f" (span={span}px)"
        hdg = info.get('grid_heading')
        if hdg is not None:
            text += f" hdg={hdg}"
        cv2.putText(display, text, (10, h - 40), font, 0.5, (255, 255, 0), 1)
    cv2.putText(display, f"{info.get('fps', 0.0):.1f} FPS", (w - 100, 25), font, 0.5, (200, 200, 200), 1)
    return display


def _read_camera_direct():
    """Grab a fresh frame straight from the camera while the agent is paused.
    Serialized so concurrent /video clients don't read the device at once."""
    if camera is None:
        return None
    try:
        with _direct_read_lock:
            ok, frame = camera.read()
        if ok and frame is not None:
            return frame
    except Exception:
        pass
    return None


def _encode_frame(display):
    ret, jpeg = cv2.imencode('.jpg', display, [cv2.IMWRITE_JPEG_QUALITY, _STREAM_QUALITY])
    if not ret:
        return None
    return (b'--frame\r\n'
            b'Content-Type: image/jpeg\r\n\r\n' + jpeg.tobytes() + b'\r\n')


def _render_frame(frame):
    if _SHOW_MASKS and _MASKS_AVAILABLE:
        return _montage(frame)
    return visualize(cv2.resize(frame, _STREAM_SIZE, interpolation=cv2.INTER_AREA))


def generate_frames():
    import time
    global _latest_frame
    while True:
        try:
            # Agent stopped: it no longer pumps frames, so read the camera here
            # and keep the live view + HSV sampler alive while the bot is halted.
            if not _agent_alive():
                frame = _read_camera_direct()
                if frame is None:
                    blank = np.zeros((_STREAM_SIZE[1], _STREAM_SIZE[0], 3), dtype=np.uint8)
                    cv2.putText(blank, "Waiting for camera...", (90, _STREAM_SIZE[1] // 2),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (80, 80, 80), 2)
                    chunk = _encode_frame(blank)
                    if chunk:
                        yield chunk
                    time.sleep(0.05)
                    continue
                with _latest_frame_lock:
                    _latest_frame = frame
                display = _render_frame(frame)
                cv2.putText(display, "AGENT STOPPED", (10, display.shape[0] - 12),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 200, 255), 2)
                chunk = _encode_frame(display)
                if chunk:
                    yield chunk
                time.sleep(0.05)
                continue

            frame = _frame_queue.get(timeout=0.5)
            # Drain to the freshest queued frame so the stream never lags behind.
            while True:
                try:
                    frame = _frame_queue.get_nowait()
                except queue.Empty:
                    break
            with _latest_frame_lock:
                _latest_frame = frame
            display = _render_frame(frame)
            chunk = _encode_frame(display)
            if chunk:
                yield chunk
        except queue.Empty:
            blank = np.zeros((_STREAM_SIZE[1], _STREAM_SIZE[0], 3), dtype=np.uint8)
            cv2.putText(blank, "Waiting for frames...", (110, _STREAM_SIZE[1] // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (80, 80, 80), 2)
            chunk = _encode_frame(blank)
            if chunk:
                yield chunk
            time.sleep(0.05)
        except Exception as e:
            print(f'[VideoStream] Error: {e}')
            time.sleep(0.05)


# --- agent lifecycle ---------------------------------------------------------

def _agent_alive():
    return _agent_thread is not None and _agent_thread.is_alive()


def _start_agent():
    """Start the follower agent thread on the real hardware (camera, wheels and
    LEDs)."""
    global _agent_thread
    with _agent_lock:
        if _agent_alive():
            return False
        stop_event.clear()
        _agent_thread = threading.Thread(
            target=agent.main,
            args=(camera, wheels, leds, stop_event, _frame_queue, _debug_info_lock, _debug_info),
            daemon=True,
            name='FollowAgentThread',
        )
        _agent_thread.start()
        return True


def _stop_agent(timeout=3.0):
    """Signal the agent to stop and join it. Returns (was_running, stopped_now).
    The agent's finally block zeroes the wheels and turns the LEDs off, so the
    bot halts cleanly. stopped_now is False if the thread is wedged past the
    join timeout."""
    with _agent_lock:
        if not _agent_alive():
            return False, True
        stop_event.set()
        _agent_thread.join(timeout)
        return True, not _agent_thread.is_alive()


# --- routes ------------------------------------------------------------------

@app.route('/')
def index():
    return INDEX_HTML


@app.route('/video')
def video():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/status')
def status():
    with _debug_info_lock:
        info = dict(_debug_info)
    info['agent_running'] = _agent_alive()
    return jsonify(info)


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
    return jsonify(status='ok', message='agent stopped')


@app.route('/sample')
def sample():
    """Click-to-sample HSV for tuning. fx/fy are click fractions [0,1] over the
    streamed image. With the 2x2 montage the camera is the top-left quarter;
    otherwise the whole frame. Returns the median H/S/V of a small patch in the
    same BGR->HSV space the lane detector uses -- paste straight into the HSV
    config."""
    try:
        fx = float(request.args.get('fx', -1.0))
        fy = float(request.args.get('fy', -1.0))
    except ValueError:
        return jsonify(error='bad coords')
    with _latest_frame_lock:
        frame = None if _latest_frame is None else _latest_frame.copy()
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


_HSV_CONFIG_FILE = os.path.normpath(os.path.join(
    os.path.dirname(__file__), '..', '..', 'config', 'lane_servoing_hsv_config.yaml'))
_FOLLOW_CONFIG_FILE = os.path.normpath(os.path.join(
    os.path.dirname(__file__), '..', '..', 'config', 'project_follow_config.yaml'))

# Cruise-speed slider bounds (matches the sim server's /tuning).
_SPEED_BOUNDS = (0.05, 0.6)


@app.route('/hsv', methods=['GET', 'POST'])
def hsv_tuning():
    """Read or live-update the yellow/white lane HSV bounds. POSTed values apply
    to the running detector immediately (the mask panels in the montage redraw
    with them on the next frame) and persist to the YAML config so restarts keep
    them. Use the click-to-sample tool to read pixel H/S/V, then set the bounds
    around it."""
    return jsonify(handle_hsv_tuning(request, _HSV_CONFIG_FILE))


@app.route('/speed', methods=['GET', 'POST'])
def speed():
    """Read or live-set the follower's cruise speed. POSTed values apply to the
    running FSM immediately (clamped to _SPEED_BOUNDS) and persist to the YAML
    config so restarts keep them. The FSM still tapers toward 0 as the leader
    nears and uses its own pursuit/turn speeds, so this is the headline cruise
    pace, not a fixed wheel command."""
    fsm = getattr(agent, 'live', {}).get('fsm')

    if request.method == 'GET':
        return jsonify(speed=getattr(fsm, 'cruise_speed', None), bounds=list(_SPEED_BOUNDS))

    body = request.get_json(silent=True) or {}
    if body.get('speed') is None:
        return jsonify(status='error', message='no speed provided')
    try:
        lo, hi = _SPEED_BOUNDS
        val = min(hi, max(lo, float(body['speed'])))
    except (TypeError, ValueError):
        return jsonify(status='error', message='bad speed value')

    if fsm is not None:
        fsm.cruise_speed = val
    update_yaml_values(_FOLLOW_CONFIG_FILE, {'cruise_speed': val})
    note = '' if fsm is not None else ' (agent not running: saved to config only)'
    return jsonify(status='ok', message=f'cruise speed = {val:g}{note}', speed=val)


@app.route('/command', methods=['POST'])
def command():
    return jsonify({'status': 'ok'})


@app.route('/shutdown')
def shutdown():
    shutdown_cleanup(wheels, camera, stop_event)
    return jsonify({'status': 'ok'})


def main():
    global camera, wheels, leds, stop_event

    ap = argparse.ArgumentParser(description='Project Follower Server — Real Hardware')
    ap.add_argument('--port', type=int, default=5000)
    args = ap.parse_args()

    suppress_http_logs()
    print('=' * 60)
    print('PROJECT FOLLOWER SERVER — REAL HARDWARE')
    print('=' * 60)

    print('\n[1/4] Initializing LED driver...')
    try:
        leds = LEDDriver()
        leds.all_off()
        print('  LEDs: ok')
    except Exception as e:
        print(f'  LEDs: not available ({e})')
        leds = None

    print('\n[2/4] Initializing wheels driver...')
    wheels = DaguWheelsDriver(WheelPWMConfiguration(), WheelPWMConfiguration())
    print('  Wheels: ok')

    print('\n[3/4] Initializing camera driver...')
    camera = CameraDriver()
    camera.start()
    print('  Camera: ok')

    print('\n[4/4] Starting follower agent...')
    _start_agent()
    print('  agent.main() running')

    def _shutdown(signum, frame):
        print('\nShutting down...')
        if leds:
            try:
                leds.all_off(); leds.release()
            except Exception:
                pass
        shutdown_cleanup(wheels, camera, stop_event)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT,  _shutdown)

    web_port = find_available_port(args.port)
    print(f'\nVideo stream: http://localhost:{web_port}/video')
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
        shutdown_cleanup(wheels, camera, stop_event)


if __name__ == '__main__':
    sys.exit(main())
