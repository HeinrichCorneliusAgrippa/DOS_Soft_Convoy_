import sys
import os
import threading

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.join(script_dir, '..', '..')
sys.path.insert(0, project_root)

from flask import Flask, Response, render_template_string, request, jsonify
import cv2
import socket
import yaml

from tasks.visual_lane_servoing.packages.agent import LaneServoingAgent
from servers.visual_lane_servoing.visualization import create_lane_visualization
from servers.templates.lane_servoing import LANE_SERVOING_TEMPLATE as HTML_TEMPLATE

from duckiebot.wheel_driver.godot_wheels_driver import GodotWheelsDriver
from duckiebot.wheel_driver.wheels_driver_abs import WheelPWMConfiguration
from duckiebot.camera_driver.godot_camera_driver import GodotCameraDriver, GodotCameraConfig
from launcher.ports import find_available_port
from servers.common import make_frame_generator, shutdown_cleanup, suppress_http_logs

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


def visualize(frame):
    """frame is RGB from Godot camera."""
    global running
    if agent is None or wheels is None:
        return cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)

    pwm_left, pwm_right = agent.compute_commands(frame)
    if running:
        wheels.set_wheels_speed(pwm_left, pwm_right)
    else:
        wheels.set_wheels_speed(0.0, 0.0)
    debug_info = agent.last_debug_info

    bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
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
