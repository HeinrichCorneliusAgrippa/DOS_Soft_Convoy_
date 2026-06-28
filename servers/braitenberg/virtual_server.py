import sys
import os
import threading
import time

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.join(script_dir, '..', '..')
sys.path.insert(0, project_root)

from flask import Flask, Response, render_template_string, request, jsonify
import cv2
import numpy as np
from tasks.braitenberg.packages.agent import BraitenbergAgent, BraitenbergAgentConfig
import tasks.braitenberg.packages.preprocessing as preprocessing_module
from servers.templates.braitenberg import BRAITENBERG_TEMPLATE as HTML_TEMPLATE

from duckiebot.wheel_driver.godot_wheels_driver import GodotWheelsDriver, GameState
from duckiebot.wheel_driver.wheels_driver_abs import WheelPWMConfiguration
from duckiebot.camera_driver.godot_camera_driver import GodotCameraDriver, GodotCameraConfig
from launcher.ports import find_available_port
from servers.common import make_frame_generator, shutdown_cleanup, suppress_http_logs

import yaml

HSV_CONFIG_FILE = os.path.join(project_root, 'config', 'braitenberg_hsv_config.yaml')
BRAITENBERG_CONFIG_FILE = os.path.join(project_root, 'config', 'braitenberg_config.yaml')


def save_braitenberg_config(gain, const, threshold):
    try:
        data = {
            'gain': float(gain),
            'const': float(const),
            'detection_threshold': float(threshold)
        }
        os.makedirs(os.path.dirname(BRAITENBERG_CONFIG_FILE), exist_ok=True)
        with open(BRAITENBERG_CONFIG_FILE, 'w') as f:
            yaml.dump(data, f, default_flow_style=False)
        print(f"[Braitenberg] Saved config to {BRAITENBERG_CONFIG_FILE}")
        return True
    except Exception as e:
        print(f"[Braitenberg] Could not save config: {e}")
        return False


def load_braitenberg_config():
    try:
        if os.path.exists(BRAITENBERG_CONFIG_FILE):
            with open(BRAITENBERG_CONFIG_FILE, 'r') as f:
                data = yaml.safe_load(f)
            if data:
                print(f"[Braitenberg] Loaded config from {BRAITENBERG_CONFIG_FILE}")
                return data
    except Exception as e:
        print(f"[Braitenberg] Could not load config: {e}")
    return {'gain': 0.9, 'const': 1.0, 'detection_threshold': 1000.0}

app = Flask(__name__)

camera = None
wheels = None
agent = None
config = None
last_frame_info = {}
game_stats = {
    'game_over': False,
    'survival_time': 0.0,
    'distance_traveled': 0.0,
    'distance_from_start': 0.0,
    'running': False
}
control_thread = None
stop_event = threading.Event()


def posneg_colormap(matrix):
    """Create red/blue colormap for positive/negative values."""
    if matrix.max() == 0 and matrix.min() == 0:
        return np.zeros((*matrix.shape, 3), dtype=np.uint8)

    normalized = matrix / (np.abs(matrix).max() + 1e-8)
    rgb = np.zeros((*matrix.shape, 3), dtype=np.uint8)
    rgb[:, :, 2] = (np.maximum(0, normalized) * 255).astype(np.uint8)   # Red
    rgb[:, :, 0] = (np.maximum(0, -normalized) * 255).astype(np.uint8)  # Blue
    return rgb


_last_status_print = 0.0

def create_visualization(frame):
    """Create 2x2 visualization grid with matrices like real server."""
    global agent, wheels, last_frame_info, game_stats, _last_status_print

    if frame is None:
        placeholder = np.zeros((240, 640, 3), dtype=np.uint8)
        cv2.putText(placeholder, "Waiting for Godot...", (200, 120),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (100, 100, 100), 2)
        return placeholder

    # Get debug info
    debug_info = agent.get_debug_info(frame)

    # Run agent and get PWM (only if not game over)
    if not game_stats['game_over']:
        pwm_left, pwm_right = agent.step(frame, wheels)
    else:
        pwm_left, pwm_right = 0.0, 0.0
        wheels.set_wheels_speed(0, 0)

    # Store for display (cast to Python float — numpy float32 is not JSON serializable)
    last_frame_info = {
        'pwm_left': float(pwm_left),
        'pwm_right': float(pwm_right),
        'left_sum': float(debug_info['left_sum']),
        'right_sum': float(debug_info['right_sum']),
        'diff': float(debug_info['diff'])
    }

    import time as _time
    now = _time.time()
    if now - _last_status_print >= 3.0:
        _last_status_print = now
        total_det = float(debug_info['total_detection'])
        det_flag  = ' [below threshold]' if debug_info['noise_filtered'] else ''
        v_val     = float(debug_info['v'])
        omega_val = float(debug_info['omega'])
        gs = game_stats
        game_info = (f" | GAME OVER t={gs['survival_time']:.1f}s"
                     if gs['game_over'] else
                     f" | t={gs['survival_time']:.1f}s dist={gs['distance_traveled']:.1f}m")
        print(f"[Braitenberg] det={total_det:.0f}px{det_flag} | "
              f"L={pwm_left:+.3f}  R={pwm_right:+.3f} | "
              f"v={v_val:.2f}  ω={omega_val:+.2f} | "
              f"gain={config.gain:.2f}  const={config.const:.2f}  thr={config.detection_threshold:.0f}"
              f"{game_info}")

    # Resize
    h, w = frame.shape[:2]
    display_w = 320
    display_h = int(h * display_w / w)

    # 1. Original camera (RGB to BGR for OpenCV)
    img_original = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    img_original = cv2.resize(img_original, (display_w, display_h))

    # 2. Duckie detection heatmap
    preprocessed = debug_info['preprocessed']
    preprocessed_color = cv2.applyColorMap(
        (preprocessed * 255).astype(np.uint8),
        cv2.COLORMAP_HOT
    )
    preprocessed_color = cv2.resize(preprocessed_color, (display_w, display_h))

    # 3. Left weight matrix
    left_matrix_rgb = posneg_colormap(agent.left_matrix)
    left_matrix_rgb = cv2.resize(left_matrix_rgb, (display_w, display_h))

    # 4. Right weight matrix
    right_matrix_rgb = posneg_colormap(agent.right_matrix)
    right_matrix_rgb = cv2.resize(right_matrix_rgb, (display_w, display_h))

    # Create 2x2 grid
    top_row = np.hstack([img_original, preprocessed_color])
    bottom_row = np.hstack([left_matrix_rgb, right_matrix_rgb])
    combined = np.vstack([top_row, bottom_row])

    # Add labels
    font = cv2.FONT_HERSHEY_SIMPLEX
    color = (0, 255, 0)
    cv2.putText(combined, "Camera", (10, 20), font, 0.5, color, 1)
    cv2.putText(combined, "Duckie Detection", (display_w + 10, 20), font, 0.5, color, 1)
    cv2.putText(combined, "Left Matrix", (10, display_h + 20), font, 0.5, color, 1)
    cv2.putText(combined, "Right Matrix", (display_w + 10, display_h + 20), font, 0.5, color, 1)

    # Game over overlay on video
    if game_stats['game_over']:
        cv2.putText(combined, "GAME OVER!", (display_w - 60, display_h + 20), font, 0.7, (0, 0, 255), 2)

    return combined


def control_loop():
    """Background thread that reads frames and updates game state."""
    global camera, wheels, agent, game_stats, stop_event

    print("[ControlLoop] Starting...")
    start_time = time.time()

    while not stop_event.is_set():
        try:
            # Update game stats from wheels driver
            if wheels:
                state = wheels.game_state
                game_stats['game_over'] = state.game_over
                if state.game_over:
                    game_stats['survival_time'] = state.survival_time
                    game_stats['distance_traveled'] = state.distance_traveled
                    game_stats['distance_from_start'] = state.distance_from_start
                else:
                    game_stats['survival_time'] = time.time() - start_time

            time.sleep(0.05)  # 20 Hz update

        except Exception as e:
            print(f"[ControlLoop] Error: {e}")
            time.sleep(0.1)

    print("[ControlLoop] Stopped")


generate_frames = make_frame_generator(lambda: camera, create_visualization, quality=50)


@app.route('/')
def index():
    return render_template_string(
        HTML_TEMPLATE,
        config=config,
        title="Braitenberg Agent - Virtual",
        subtitle="Godot Simulation - Avoid the Ducks!",
        show_game_stats=True  # Show game stats for simulation
    )


@app.route('/video')
def video():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/get_stats')
def get_stats():
    global game_stats, camera
    stats = game_stats.copy()
    stats['running'] = camera is not None
    return jsonify(stats)


@app.route('/reset_game', methods=['POST'])
def reset_game():
    global wheels, game_stats
    if wheels:
        wheels.reset_game()
    game_stats = {
        'game_over': False,
        'survival_time': 0.0,
        'distance_traveled': 0.0,
        'distance_from_start': 0.0,
        'running': True
    }
    return jsonify({'status': 'ok'})


@app.route('/get_hsv')
def get_hsv():
    lo = preprocessing_module.lower_hsv
    hi = preprocessing_module.upper_hsv
    return jsonify({'lower_h': int(lo[0]), 'lower_s': int(lo[1]), 'lower_v': int(lo[2]),
                    'upper_h': int(hi[0]), 'upper_s': int(hi[1]), 'upper_v': int(hi[2])})


@app.route('/update_hsv', methods=['POST'])
def update_hsv():
    data = request.json
    preprocessing_module.lower_hsv = np.array([int(data['lower_h']), int(data['lower_s']), int(data['lower_v'])])
    preprocessing_module.upper_hsv = np.array([int(data['upper_h']), int(data['upper_s']), int(data['upper_v'])])
    try:
        with open(HSV_CONFIG_FILE, 'w') as f:
            yaml.dump({k: int(v) for k, v in data.items()}, f, default_flow_style=False)
    except Exception as e:
        print(f"[HSV] Could not save config: {e}")
    return jsonify({'status': 'ok'})


@app.route('/get_motors')
def get_motors():
    return jsonify(last_frame_info)


@app.route('/update_config', methods=['POST'])
def update_config():
    global config
    data = request.json
    config.gain = float(data['gain'])
    config.const = float(data['const'])
    config.detection_threshold = float(data['detection_threshold'])

    # Auto-save to disk
    save_braitenberg_config(config.gain, config.const, config.detection_threshold)

    return jsonify({'status': 'ok'})


def main():
    global camera, wheels, agent, config, control_thread, stop_event

    import argparse
    ap = argparse.ArgumentParser(description="Virtual Braitenberg Web Server for Godot")
    ap.add_argument("--port", type=int, default=5000, help="Web server port")
    ap.add_argument("--frame-port", type=int, default=5001, help="Godot camera port")
    ap.add_argument("--wheel-port", type=int, default=5002, help="Godot wheel port")
    ap.add_argument("--godot-host", type=str, default="localhost", help="Godot host")
    ap.add_argument("--gain", type=float, default=0.9, help="Braitenberg gain")
    ap.add_argument("--base", type=float, default=0.4, help="Base motor speed")
    ap.add_argument("--threshold", type=float, default=1000.0, help="Detection threshold")
    args = ap.parse_args()

    suppress_http_logs()
    print("=" * 60)
    print("VIRTUAL BRAITENBERG SERVER")
    print("=" * 60)

    # Load configs from disk
    print("\n[0/3] Loading configurations...")
    loaded_config = load_braitenberg_config()
    print(f"  Loaded: gain={loaded_config['gain']}, const={loaded_config['const']}, threshold={loaded_config['detection_threshold']}")

    # Initialize wheels driver
    print("\n[1/3] Initializing wheels driver...")
    left_cfg = WheelPWMConfiguration()
    right_cfg = WheelPWMConfiguration()
    wheels = GodotWheelsDriver(
        left_cfg,
        right_cfg,
        godot_host=args.godot_host,
        godot_port=args.wheel_port,
    )
    wheels.trim = 0  # simulation wheels are symmetric, no trim needed
    print(f"  Wheels: {args.godot_host}:{args.wheel_port}")

    # Initialize camera driver
    print("\n[2/3] Initializing camera driver...")
    print(f"  Waiting for Godot to connect on port {args.frame_port}...")
    camera_cfg = GodotCameraConfig(host="0.0.0.0", port=args.frame_port)
    camera = GodotCameraDriver(godot_config=camera_cfg)
    camera.start()  # Start camera and wait for Godot connection
    print(f"  Camera: connected!")

    # Create agent with loaded config (command line args can override)
    print("\n[3/3] Creating Braitenberg agent...")
    config = BraitenbergAgentConfig(
        gain=loaded_config['gain'],
        const=loaded_config['const'],
        detection_threshold=loaded_config['detection_threshold']
    )
    agent = BraitenbergAgent(config)
    print(f"  Gain: {config.gain}, Base: {config.const}, Threshold: {config.detection_threshold}")

    # Start control thread
    stop_event.clear()
    control_thread = threading.Thread(target=control_loop, daemon=True)
    control_thread.start()

    # Start web server
    web_port = find_available_port(args.port)
    if web_port != args.port:
        print(f"  Port {args.port} busy, using {web_port}")

    print("\n" + "=" * 60)
    print(f"Web Interface: http://localhost:{web_port}")
    print("=" * 60)
    print("\n1. Start Godot simulation")
    print("2. Open the web interface in your browser")
    print("3. Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        app.run(host='127.0.0.1', port=web_port, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("\n\nShutting down...")
    finally:
        shutdown_cleanup(wheels, camera, stop_event)


if __name__ == "__main__":
    sys.exit(main())
