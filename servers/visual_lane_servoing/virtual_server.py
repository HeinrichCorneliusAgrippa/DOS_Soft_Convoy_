import sys
import os
import threading

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.join(script_dir, '..', '..')
sys.path.insert(0, project_root)

from flask import Flask, Response, render_template_string, request, jsonify
import cv2
import numpy as np
import socket
import yaml

from tasks.visual_lane_servoing.packages.agent import LaneServoingAgent
from servers.visual_lane_servoing.visualization import create_lane_visualization
from servers.templates.lane_servoing import LANE_SERVOING_TEMPLATE as HTML_TEMPLATE

from duckiebot.wheel_driver.godot_wheels_driver import GodotWheelsDriver
from duckiebot.wheel_driver.wheels_driver_abs import WheelPWMConfiguration
from duckiebot.camera_driver.godot_camera_driver import GodotCameraDriver, GodotCameraConfig
from launcher.ports import find_available_port
from servers.common import make_frame_generator, shutdown_cleanup, suppress_http_logs, LatestFrame

LANE_CONFIG_FILE = os.path.join(project_root, 'config', 'lane_servoing_config.yaml')
LANE_HSV_CONFIG_FILE = os.path.join(project_root, 'config', 'lane_servoing_hsv_config.yaml')


def _get_student_module():
    from tasks.visual_lane_servoing.packages import visual_servoing_activity
    return visual_servoing_activity


app = Flask(__name__)

camera  = None
wheels  = None
agent   = None
running = False
stop_event = threading.Event()

# Newest raw camera frame in BGR (the same space the detector samples), kept so
# /sample can read true pixel HSV under the click. The montage that /video shows
# is a resized copy of this frame, so click fractions map back to it exactly.
_latest_raw = LatestFrame()


def visualize(frame):
    """frame is RGB from Godot camera."""
    global running
    bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    _latest_raw.put_nowait(bgr)

    if agent is None or wheels is None:
        return bgr

    pwm_left, pwm_right = agent.compute_commands(frame)
    if running:
        wheels.set_wheels_speed(pwm_left, pwm_right)
    else:
        wheels.set_wheels_speed(0.0, 0.0)
    debug_info = agent.last_debug_info

    return create_lane_visualization(bgr, debug_info, pwm_left, pwm_right)


generate_frames = make_frame_generator(lambda: camera, visualize, quality=50)


@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE, config=agent, hostname=socket.gethostname())


@app.route('/video')
def video():
    return Response(generate_frames(), mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/reset', methods=['POST'])
def reset():
    if wheels is not None:
        wheels.reset_game()
    if agent is not None:
        agent._last_steering = 0.0
    if wheels is not None and agent is not None:
        spd = agent.base_speed
        wheels.set_wheels_speed(spd, spd)
    return jsonify({'status': 'ok'})


@app.route('/update_config', methods=['POST'])
def update_config():
    data = request.json
    agent.p_gain     = float(data.get('k_d',   agent.p_gain))
    agent.d_gain     = float(data.get('k_phi', agent.d_gain))
    agent.base_speed = float(data.get('const', agent.base_speed))
    try:
        with open(LANE_CONFIG_FILE, 'r') as f:
            saved = yaml.safe_load(f) or {}
        saved['p_gain']     = agent.p_gain
        saved['d_gain']     = agent.d_gain
        saved['base_speed'] = agent.base_speed
        with open(LANE_CONFIG_FILE, 'w') as f:
            yaml.dump(saved, f, default_flow_style=False)
    except Exception as e:
        print(f"[LaneServoing] Could not save config: {e}")
    return jsonify({'status': 'ok'})


@app.route('/get_hsv')
def get_hsv():
    return jsonify(_get_student_module().get_hsv_bounds())


@app.route('/update_hsv', methods=['POST'])
def update_hsv():
    data = request.json
    mod = _get_student_module()
    current = mod.get_hsv_bounds()
    current.update({k: int(v) for k, v in data.items()})
    mod.set_hsv_bounds(
        [current['yellow_lower_h'], current['yellow_lower_s'], current['yellow_lower_v']],
        [current['yellow_upper_h'], current['yellow_upper_s'], current['yellow_upper_v']],
        [current['white_lower_h'],  current['white_lower_s'],  current['white_lower_v']],
        [current['white_upper_h'],  current['white_upper_s'],  current['white_upper_v']],
    )
    try:
        with open(LANE_HSV_CONFIG_FILE, 'w') as f:
            yaml.dump(current, f, default_flow_style=False)
    except Exception as e:
        print(f"[LaneServoing] Could not save HSV config: {e}")
    return jsonify({'status': 'ok'})


@app.route('/sample')
def sample():
    """Click-to-sample HSV for tuning. fx/fy are click fractions [0,1] over the
    streamed montage. The Camera panel is the top-left cell of the 2x2 grid,
    which sits above a 120px info strip — so it spans the left half in x and
    only display_h/(2*display_h+120) in y, not a clean quarter. Returns the
    median H/S/V of a small patch in the same BGR->HSV space the lane detector
    uses, so the numbers paste straight into the HSV bounds."""
    try:
        fx = float(request.args.get('fx', -1.0))
        fy = float(request.args.get('fy', -1.0))
    except ValueError:
        return jsonify(error='bad coords')

    frame = _latest_raw.get_latest()
    frame = None if frame is None else frame.copy()
    if frame is None:
        return jsonify(error='no frame yet')

    h_img, w_img = frame.shape[:2]
    display_h = int(h_img * 320 / w_img)         # panel height in the montage
    panel_fx_max = 0.5                            # camera is the left half in x
    panel_fy_max = display_h / (2 * display_h + 120)
    if not (0.0 <= fx < panel_fx_max and 0.0 <= fy < panel_fy_max):
        return jsonify(hint='click the Camera panel (top-left) to sample')

    sx = fx / panel_fx_max
    sy = fy / panel_fy_max
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


@app.route('/start', methods=['POST'])
def start():
    global running
    running = True
    print("[Control] Started")
    return jsonify({'status': 'running'})


@app.route('/stop', methods=['POST'])
def stop():
    global running, wheels
    running = False
    if wheels:
        wheels.set_wheels_speed(0.0, 0.0)
    print("[Control] Stopped")
    return jsonify({'status': 'stopped'})


@app.route('/running')
def get_running():
    return jsonify({'running': running})


@app.route('/status')
def status():
    if agent is None:
        return jsonify({'status': 'not_initialized'})
    return jsonify({
        'status': 'active',
        'frame_count': agent.frame_count,
        'config': {'p_gain': agent.p_gain, 'd_gain': agent.d_gain,
                   'base_speed': agent.base_speed, 'detection_threshold': agent.detection_threshold},
    })


def main():
    global camera, wheels, agent

    import argparse
    ap = argparse.ArgumentParser(description="Virtual Lane Servoing Server")
    ap.add_argument("--port",       type=int, default=5000)
    ap.add_argument("--frame-port", type=int, default=5001)
    ap.add_argument("--wheel-port", type=int, default=5002)
    ap.add_argument("--godot-host", type=str, default="localhost")
    args = ap.parse_args()

    suppress_http_logs()
    print("=" * 60)
    print("VIRTUAL LANE SERVOING SERVER")
    print("=" * 60)

    print("\n[1/3] Initializing wheels driver...")
    wheels = GodotWheelsDriver(
        WheelPWMConfiguration(pwm_min=0), WheelPWMConfiguration(pwm_min=0),
        godot_host=args.godot_host,
        godot_port=args.wheel_port,
    )
    wheels.trim = 0
    print(f"  Wheels: {args.godot_host}:{args.wheel_port}")

    print("\n[2/3] Initializing camera driver...")
    print(f"  Waiting for Godot on port {args.frame_port}...")
    camera = GodotCameraDriver(godot_config=GodotCameraConfig(host="0.0.0.0", port=args.frame_port))
    camera.start()
    print("  Camera: connected!")

    print("\n[3/3] Creating agent...")
    agent = LaneServoingAgent()
    print(f"  p_gain={agent.p_gain}, d_gain={agent.d_gain}, base_speed={agent.base_speed}")

    web_port = find_available_port(args.port)
    if web_port != args.port:
        print(f"  Port {args.port} busy, using {web_port}")

    print("\n" + "=" * 60)
    print(f"Web Interface: http://localhost:{web_port}")
    print("=" * 60 + "\n")

    try:
        app.run(host='127.0.0.1', port=web_port, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        shutdown_cleanup(wheels, camera, stop_event)


if __name__ == "__main__":
    sys.exit(main())
