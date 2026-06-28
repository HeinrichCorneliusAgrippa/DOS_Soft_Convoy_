import sys
import os
import threading
import time
import queue
import socket

script_dir   = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.join(script_dir, '..', '..')
sys.path.insert(0, project_root)

import cv2
from flask import Flask, Response, render_template_string, jsonify, request

from tasks.visual_lane_servoing.packages.agent import LaneServoingAgent
from tasks.object_detection.packages.agent import ObjectDetectionAgent, CLASS_NAMES
from tasks.object_detection.packages.stop_activity import should_stop as student_should_stop
from servers.object_detection.visualization import draw_detections
from servers.templates.object_detection import OBJECT_DETECTION_TEMPLATE as HTML_TEMPLATE

from duckiebot.camera_driver.godot_camera_driver import GodotCameraDriver, GodotCameraConfig
from duckiebot.wheel_driver.godot_wheels_driver import GodotWheelsDriver
from duckiebot.wheel_driver.wheels_driver_abs import WheelPWMConfiguration
from launcher.ports import find_available_port
from launcher.config import GODOT_SCENES
from servers.common import make_frame_generator, shutdown_cleanup, suppress_http_logs


app        = Flask(__name__)
lane_agent = None
det_agent  = None
camera     = None
wheels     = None
running    = False
manual_mode = False
stop_event = threading.Event()

_frame_queue     = queue.Queue(maxsize=1)
_last_detections = []
_detection_lock  = threading.Lock()
_stopped_by_det  = False
_stop_reason     = ''

keys_pressed     = {'up': False, 'down': False, 'left': False, 'right': False}
_keys_lock       = threading.Lock()
_keys_last_update = time.time()

_current_scene   = 'object_detection'


def detection_loop():
    global _last_detections
    while not stop_event.is_set():
        if det_agent is None or not det_agent.model_loaded:
            time.sleep(0.1)
            continue
        try:
            frame_rgb = _frame_queue.get(timeout=0.5)
        except queue.Empty:
            continue
        result = det_agent.detect(frame_rgb)
        if result is not None:
            with _detection_lock:
                _last_detections = result


def manual_control_loop():
    global _keys_last_update
    while not stop_event.is_set():
        if not manual_mode or not wheels:
            time.sleep(0.05)
            continue

        if time.time() - _keys_last_update > 0.5:
            with _keys_lock:
                for k in keys_pressed:
                    keys_pressed[k] = False

        with _keys_lock:
            kc = keys_pressed.copy()

        left = right = 0.0
        if kc['up']:
            left, right = 0.5, 0.5
        if kc['down']:
            left, right = -0.5, -0.5
        if kc['up'] and kc['left']:
            left, right = 0.2, 0.5
        elif kc['up'] and kc['right']:
            left, right = 0.5, 0.2
        elif kc['left']:
            left, right = -0.3, 0.3
        elif kc['right']:
            left, right = 0.3, -0.3

        if not (wheels.is_game_over()):
            wheels.set_wheels_speed(left, right)

        time.sleep(0.05)


def _should_stop(detections):
    if det_agent is None:
        return False, ''
    return student_should_stop(detections, det_agent.img_size)


def visualize(frame_rgb):
    global _stopped_by_det, _stop_reason

    bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)

    if wheels is None:
        return bgr

    # Push frame to detection queue
    if det_agent is not None and det_agent.model_loaded:
        small = cv2.resize(frame_rgb, (det_agent.img_size, det_agent.img_size))
        try:
            _frame_queue.put_nowait(small)
        except queue.Full:
            pass

    with _detection_lock:
        detections = list(_last_detections)

    if manual_mode:
        _stopped_by_det = False
        _stop_reason    = ''
    elif lane_agent is not None:
        pwm_left, pwm_right = lane_agent.compute_commands(frame_rgb)

        should_stop_flag, reason = _should_stop(detections)
        _stopped_by_det = should_stop_flag
        _stop_reason    = reason

        if running and not should_stop_flag and not wheels.is_game_over():
            wheels.set_wheels_speed(pwm_left, pwm_right)
        else:
            wheels.set_wheels_speed(0.0, 0.0)

    if det_agent is not None and det_agent.model_loaded and detections:
        oh, ow = bgr.shape[:2]
        sx = ow / det_agent.img_size
        sy = oh / det_agent.img_size
        scaled = [((int(x1*sx), int(y1*sy), int(x2*sx), int(y2*sy)), s, c)
                  for (x1, y1, x2, y2), s, c in detections]
        draw_detections(bgr, scaled)

    return bgr


generate_frames = make_frame_generator(lambda: camera, visualize, quality=50)


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, config=det_agent, hostname=socket.gethostname(), virtual=True)

@app.route('/video')
def video():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')

@app.route('/start', methods=['POST'])
def start():
    global running
    running = True
    return jsonify({'status': 'running'})

@app.route('/stop', methods=['POST'])
def stop():
    global running
    running = False
    if wheels:
        wheels.set_wheels_speed(0.0, 0.0)
    return jsonify({'status': 'stopped'})

@app.route('/reset', methods=['POST'])
def reset():
    global _stopped_by_det, _stop_reason, _last_detections, running
    if wheels:
        wheels.reset_game()
    _stopped_by_det = False
    _stop_reason    = ''
    running         = True
    with _detection_lock:
        _last_detections = []
    return jsonify({'status': 'reset', 'running': running})

@app.route('/set_mode', methods=['POST'])
def set_mode():
    global manual_mode
    mode = request.json.get('mode', 'auto') if request.json else 'auto'
    manual_mode = (mode == 'manual')
    if wheels and not manual_mode:
        wheels.set_wheels_speed(0.0, 0.0)
    return jsonify({'mode': 'manual' if manual_mode else 'auto'})

@app.route('/switch_scene', methods=['POST'])
def switch_scene():
    global manual_mode, _current_scene
    target = request.json.get('scene', '') if request.json else ''
    if target not in GODOT_SCENES:
        return jsonify({'error': f'unknown scene {target!r}'}), 400
    if wheels:
        wheels.change_scene(GODOT_SCENES[target])
    _current_scene = target
    # Auto-set drive mode based on scene
    if target == 'introduction':
        manual_mode = True
    else:
        manual_mode = False
        if wheels:
            wheels.set_wheels_speed(0.0, 0.0)
    return jsonify({'scene': target, 'manual_mode': manual_mode})

@app.route('/keys', methods=['POST'])
def update_keys():
    global _keys_last_update
    data = request.json or {}
    with _keys_lock:
        for k in keys_pressed:
            keys_pressed[k] = bool(data.get(k, False))
    _keys_last_update = time.time()
    return jsonify({'status': 'ok'})

@app.route('/remove_objects', methods=['POST'])
def remove_objects():
    global _stopped_by_det, _stop_reason, _last_detections
    name_filter = request.json.get('filter', '') if request.json else ''
    if wheels and name_filter:
        wheels.remove_objects(name_filter)
    _stopped_by_det = False
    _stop_reason    = ''
    with _detection_lock:
        _last_detections = []
    return jsonify({'status': 'ok', 'filter': name_filter})

@app.route('/set_threshold', methods=['POST'])
def set_threshold():
    value = request.json.get('value') if request.json else None
    if det_agent and value is not None:
        det_agent.conf_threshold = float(value)
    return jsonify({'conf_threshold': det_agent.conf_threshold if det_agent else None})

@app.route('/status')
def status():
    with _detection_lock:
        dets = list(_last_detections)
    return jsonify({
        'running':              running,
        'manual_mode':          manual_mode,
        'current_scene':        _current_scene,
        'game_over':            wheels.is_game_over() if wheels else False,
        'model_loaded':         det_agent.model_loaded if det_agent else False,
        'load_error':           det_agent.load_error if det_agent else None,
        'trt_building':         getattr(det_agent, 'trt_building', False) if det_agent else False,
        'stopped_by_detection': _stopped_by_det,
        'stop_reason':          _stop_reason,
        'conf_threshold':       det_agent.conf_threshold if det_agent else 0.5,
        'detections': [
            {'class': CLASS_NAMES.get(c, str(c)), 'score': round(s, 3), 'bbox': list(b)}
            for b, s, c in dets
        ],
    })


def main():
    global lane_agent, det_agent, camera, wheels

    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--port',       type=int, default=5000)
    ap.add_argument('--frame-port', type=int, default=5001)
    ap.add_argument('--wheel-port', type=int, default=5002)
    ap.add_argument('--godot-host', type=str, default='localhost')
    args = ap.parse_args()

    suppress_http_logs()
    print('=' * 60)
    print('OBJECT DETECTION — LANE FOLLOW + STOP ON DETECTION')
    print('=' * 60)

    print('\n[1/4] Creating lane agent...')
    lane_agent = LaneServoingAgent()
    print(f'  p_gain={lane_agent.p_gain}, d_gain={lane_agent.d_gain}, speed={lane_agent.base_speed}')

    print('\n[2/4] Loading detection model...')
    det_agent = ObjectDetectionAgent()
    if det_agent.model_loaded:
        print(f'  Model ready: {det_agent.img_size}px')
    else:
        print(f'  WARNING: {det_agent.load_error}')

    print('\n[3/4] Initializing wheels...')
    wheels = GodotWheelsDriver(
        WheelPWMConfiguration(pwm_min=0), WheelPWMConfiguration(pwm_min=0),
        godot_host=args.godot_host, godot_port=args.wheel_port,
    )

    print('\n[4/4] Initializing camera...')
    camera = GodotCameraDriver(godot_config=GodotCameraConfig(host='0.0.0.0', port=args.frame_port))
    camera.start()

    threading.Thread(target=detection_loop,     daemon=True).start()
    threading.Thread(target=manual_control_loop, daemon=True).start()

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
