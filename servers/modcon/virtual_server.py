import sys
import os
import threading
import time
import numpy as np

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.join(script_dir, '..', '..')
sys.path.insert(0, project_root)

from flask import Flask, Response, render_template_string, request, jsonify
import cv2
import yaml

from servers.templates.modcon import MODCON_TEMPLATE as HTML_TEMPLATE
from duckiebot.wheel_driver.godot_wheels_driver import GodotWheelsDriver
from duckiebot.wheel_driver.wheels_driver_abs import WheelPWMConfiguration
from duckiebot.camera_driver.godot_camera_driver import GodotCameraDriver, GodotCameraConfig
from launcher.ports import find_available_port
from servers.common import make_frame_generator, shutdown_cleanup, suppress_http_logs

from tasks.modcon.packages.odometry_activity import delta_phi, pose_estimation
from tasks.modcon.packages.pid_controller import PIDController
import tasks.modcon.packages.pid_controller as pid_module

MODCON_CONFIG_FILE = os.path.join(project_root, 'config', 'modcon_config.yaml')


def _load_robot_params():
    try:
        with open(MODCON_CONFIG_FILE) as f:
            cfg = yaml.safe_load(f) or {}
    except Exception:
        cfg = {}
    return {
        'v_0':                cfg.get('v_0',      0.3),
        'R':                  cfg.get('radius',   0.0318),
        'baseline':           cfg.get('baseline', 0.1),
        'encoder_resolution': 135,
    }

app = Flask(__name__)

# ── Hardware ──────────────────────────────────────────────────────────────
camera = None
wheels = None
stop_event = threading.Event()

CONTROL_DT     = 0.1   # 10 Hz
TURN_DEADBAND  = 0.035 # ~2° — stop commanding omega within this band to prevent twitching

# ── Odometry ──────────────────────────────────────────────────────────────
class OdometryState:
    """Tracks robot pose using student's odometry implementation."""

    def __init__(self, R=0.0318, baseline=0.1, encoder_resolution=135):
        self.R = R
        self.baseline = baseline
        self.encoder_resolution = encoder_resolution
        self.x = 0.0
        self.y = 0.0
        self.theta = 0.0
        self.ticks_left = 0
        self.ticks_right = 0
        self.prev_ticks_left = 0
        self.prev_ticks_right = 0
        self.last_update_time = time.time()

    def reset_pose(self, x=0.0, y=0.0, theta=0.0):
        self.x = x
        self.y = y
        self.theta = theta
        self.ticks_left = 0
        self.ticks_right = 0
        self.prev_ticks_left = 0
        self.prev_ticks_right = 0
        self.last_update_time = time.time()

    def _estimate_ticks_from_pwm(self, left_pwm, right_pwm, dt):
        # Godot's max_speed=1.0 m/s; real Duckiebot max wheel speed ≈ 0.2 m/s (k=135).
        # Simulation wheels move 5× faster, so k scales accordingly:
        # k = max_speed / R * resolution / (2π) = 1.0 / 0.0318 * 135 / (2π) ≈ 676
        k = 676  # ticks/second at executed_pwm=1.0 (simulation only)
        dl = int(k * abs(left_pwm) * dt)
        dr = int(k * abs(right_pwm) * dt)
        if left_pwm < 0:
            dl = -dl
        if right_pwm < 0:
            dr = -dr
        self.ticks_left += dl
        self.ticks_right += dr

    def update(self, left_pwm, right_pwm):
        now = time.time()
        dt = now - self.last_update_time
        self.last_update_time = now

        self._estimate_ticks_from_pwm(left_pwm, right_pwm, dt)

        dphi_left, self.prev_ticks_left = delta_phi(
            self.ticks_left, self.prev_ticks_left, self.encoder_resolution
        )
        dphi_right, self.prev_ticks_right = delta_phi(
            self.ticks_right, self.prev_ticks_right, self.encoder_resolution
        )

        self.x, self.y, self.theta = pose_estimation(
            self.R, self.baseline,
            self.x, self.y, self.theta,
            dphi_left, dphi_right
        )


class PIDStateTracker:
    def __init__(self):
        self.prev_e = 0.0
        self.prev_int = 0.0

    def reset(self):
        self.prev_e = 0.0
        self.prev_int = 0.0


# ── Global State ──────────────────────────────────────────────────────────
odometry = OdometryState()
pid_state = PIDStateTracker()

robot_params = _load_robot_params()

status_lock = threading.Lock()
status = {
    'maneuver': 'idle',
    'message': '',
    'pid_error_deg': 0.0,
    'omega': 0.0,
}

path_lock = threading.Lock()
path_history = []  # list of [x, y]
MAX_PATH_POINTS = 600

pid_history_lock = threading.Lock()
pid_history = []
MAX_PID_HISTORY = 500
_pid_history_t = 0.0

maneuver_thread = None
maneuver_stop = threading.Event()


# ── Helper Functions ──────────────────────────────────────────────────────
def record_pid(theta_rad, theta_ref_rad, error_rad, omega):
    global _pid_history_t
    with pid_history_lock:
        pid_history.append({
            't':     round(_pid_history_t, 2),
            'theta': round(float(np.rad2deg(theta_rad)), 2),
            'ref':   round(float(np.rad2deg(theta_ref_rad)), 2),
            'error': round(float(np.rad2deg(error_rad)), 2),
            'omega': round(float(omega), 3),
        })
        if len(pid_history) > MAX_PID_HISTORY:
            pid_history.pop(0)
        _pid_history_t += CONTROL_DT


def pwm_from_velocity(v, omega):
    """Convert linear/angular velocity to wheel PWM using current robot params."""
    R = robot_params['R']
    baseline = robot_params['baseline']
    v_left = (v - omega * baseline / 2.0) / R
    v_right = (v + omega * baseline / 2.0) / R
    return float(np.clip(v_left, -1.0, 1.0)), float(np.clip(v_right, -1.0, 1.0))


def update_odometry_and_path():
    """Read current PWM from wheels, update odometry, append to path."""
    if wheels:
        odometry.update(wheels.left_pwm, wheels.right_pwm)
    with path_lock:
        path_history.append([round(odometry.x, 4), round(odometry.y, 4)])
        if len(path_history) > MAX_PATH_POINTS:
            path_history.pop(0)


# ── Maneuver Primitives (blocking, run inside maneuver threads) ───────────
def _turn_to(target_rad, stop_ev, tolerance_deg=5.0, timeout=12.0):
    """Turn to target heading using PID. Returns when converged or timed out."""
    pid_state.reset()
    recent_errors = []
    deadline = time.time() + timeout

    while not stop_ev.is_set() and time.time() < deadline:
        update_odometry_and_path()

        v, omega, pid_state.prev_e, pid_state.prev_int = PIDController(
            0.0, target_rad, odometry.theta,
            pid_state.prev_e, pid_state.prev_int, CONTROL_DT
        )

        error = np.arctan2(
            np.sin(target_rad - odometry.theta),
            np.cos(target_rad - odometry.theta)
        )
        recent_errors.append(abs(error))
        if len(recent_errors) > 20:
            recent_errors.pop(0)

        with status_lock:
            status['pid_error_deg'] = round(float(np.rad2deg(error)), 2)
            status['omega'] = round(float(omega), 3)

        record_pid(odometry.theta, target_rad, error, omega)

        # Deadband: within ~2° just stop — avoids endless twitching
        if abs(error) < TURN_DEADBAND:
            omega = 0.0

        # Converged: within tolerance for 1 second
        if len(recent_errors) >= 10 and max(recent_errors[-10:]) < np.deg2rad(tolerance_deg):
            break

        left_pwm, right_pwm = pwm_from_velocity(v, omega)
        if wheels:
            wheels.set_wheels_speed(left_pwm, right_pwm)

        time.sleep(CONTROL_DT)

    if wheels:
        wheels.set_wheels_speed(0, 0)
    time.sleep(0.3)


def _drive_segment(side_length, stop_ev, timeout=15.0):
    start_x, start_y = odometry.x, odometry.y
    deadline = time.time() + timeout

    while not stop_ev.is_set() and time.time() < deadline:
        update_odometry_and_path()

        dist = np.sqrt((odometry.x - start_x) ** 2 + (odometry.y - start_y) ** 2)
        error = side_length - dist

        if abs(error) < 0.02:
            break

        speed = float(np.clip(error * 0.5, -0.3, 0.3))
        if 0.0 < abs(speed) < 0.15:
            speed = 0.15 if error > 0 else 0.0

        if wheels:
            wheels.set_wheels_speed(speed, speed)

        time.sleep(CONTROL_DT)

    if wheels:
        wheels.set_wheels_speed(0, 0)
    time.sleep(0.3)


# ── Maneuver Thread Functions ─────────────────────────────────────────────
def run_straight(distance, stop_ev):
    with status_lock:
        status['maneuver'] = 'straight'
        status['message'] = f'Driving {distance:.2f} m...'

    start_x, start_y = odometry.x, odometry.y
    deadline = time.time() + 30.0

    while not stop_ev.is_set() and time.time() < deadline:
        update_odometry_and_path()

        dist = np.sqrt((odometry.x - start_x) ** 2 + (odometry.y - start_y) ** 2)
        error = distance - dist

        if abs(error) < 0.02:
            break

        speed = float(np.clip(error * 0.5, -0.3, 0.3))
        if 0.0 < abs(speed) < 0.15:
            speed = 0.15 if error > 0 else 0.0

        if wheels:
            wheels.set_wheels_speed(speed, speed)

        with status_lock:
            status['message'] = f'{dist:.3f} / {distance:.2f} m'

        time.sleep(CONTROL_DT)

    if wheels:
        wheels.set_wheels_speed(0, 0)

    dist_final = np.sqrt((odometry.x - start_x) ** 2 + (odometry.y - start_y) ** 2)
    with status_lock:
        status['maneuver'] = 'idle'
        if stop_ev.is_set():
            status['message'] = 'Stopped'
        else:
            status['message'] = f'Done: {dist_final:.3f} m (err {abs(distance - dist_final):.3f} m)'


def run_turn(target_deg, stop_ev):
    target_rad = odometry.theta + np.deg2rad(target_deg)
    target_deg_abs = np.rad2deg(target_rad)
    with status_lock:
        status['maneuver'] = 'turn'
        status['message'] = f'Turning {target_deg:+.0f}° → {target_deg_abs:.0f}°...'

    pid_state.reset()
    recent_errors = []
    deadline = time.time() + 20.0

    while not stop_ev.is_set() and time.time() < deadline:
        update_odometry_and_path()

        v, omega, pid_state.prev_e, pid_state.prev_int = PIDController(
            0.0, target_rad, odometry.theta,
            pid_state.prev_e, pid_state.prev_int, CONTROL_DT
        )

        error = np.arctan2(
            np.sin(target_rad - odometry.theta),
            np.cos(target_rad - odometry.theta)
        )
        recent_errors.append(abs(error))
        if len(recent_errors) > 20:
            recent_errors.pop(0)

        with status_lock:
            status['pid_error_deg'] = round(float(np.rad2deg(error)), 2)
            status['omega'] = round(float(omega), 3)
            status['message'] = f'{np.rad2deg(odometry.theta):.1f}° → {target_deg_abs:.0f}°'

        record_pid(odometry.theta, target_rad, error, omega)

        # Deadband: within ~2° just stop — avoids endless twitching
        if abs(error) < TURN_DEADBAND:
            omega = 0.0

        if len(recent_errors) >= 10 and max(recent_errors[-10:]) < np.deg2rad(3):
            break

        left_pwm, right_pwm = pwm_from_velocity(v, omega)
        if wheels:
            wheels.set_wheels_speed(left_pwm, right_pwm)

        time.sleep(CONTROL_DT)

    if wheels:
        wheels.set_wheels_speed(0, 0)

    final_err = np.rad2deg(np.arctan2(
        np.sin(target_rad - odometry.theta),
        np.cos(target_rad - odometry.theta)
    ))
    with status_lock:
        status['maneuver'] = 'idle'
        status['pid_error_deg'] = 0.0
        status['omega'] = 0.0
        if stop_ev.is_set():
            status['message'] = 'Stopped'
        else:
            status['message'] = f'Done: {np.rad2deg(odometry.theta):.1f}° (target {target_deg_abs:.0f}°, err {final_err:.1f}°)'


def run_square(side, stop_ev):
    with status_lock:
        status['maneuver'] = 'square'

    start_theta = odometry.theta

    for i in range(4):
        if stop_ev.is_set():
            break

        target_rad = start_theta + i * np.pi / 2
        with status_lock:
            status['message'] = f'Side {i + 1}/4: turning to {np.rad2deg(target_rad):.0f}°'

        _turn_to(target_rad, stop_ev, tolerance_deg=5.0)

        if stop_ev.is_set():
            break

        with status_lock:
            status['message'] = f'Side {i + 1}/4: driving {side} m'

        _drive_segment(side, stop_ev)

    if wheels:
        wheels.set_wheels_speed(0, 0)

    ret_dist = np.sqrt(odometry.x ** 2 + odometry.y ** 2)
    with status_lock:
        status['maneuver'] = 'idle'
        status['pid_error_deg'] = 0.0
        status['omega'] = 0.0
        if stop_ev.is_set():
            status['message'] = 'Stopped'
        else:
            status['message'] = f'Done: return dist {ret_dist:.3f} m'


def start_maneuver(fn, *args):
    global maneuver_thread, maneuver_stop
    maneuver_stop.set()
    if maneuver_thread and maneuver_thread.is_alive():
        maneuver_thread.join(timeout=1.5)
    maneuver_stop = threading.Event()
    maneuver_thread = threading.Thread(
        target=fn, args=(*args, maneuver_stop), daemon=True
    )
    maneuver_thread.start()


def stop_maneuver():
    global maneuver_stop
    maneuver_stop.set()
    if wheels:
        wheels.set_wheels_speed(0, 0)
    with status_lock:
        status['maneuver'] = 'idle'
        status['message'] = 'Stopped'
        status['pid_error_deg'] = 0.0
        status['omega'] = 0.0


# ── Visualization ─────────────────────────────────────────────────────────
def create_visualization(frame):
    if frame is None:
        placeholder = np.zeros((240, 640, 3), dtype=np.uint8)
        cv2.putText(placeholder, "Waiting for Godot...", (200, 120),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (100, 100, 100), 2)
        return placeholder

    display = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    h, w = display.shape[:2]
    display_w = 640
    display_h = int(h * display_w / w)
    display = cv2.resize(display, (display_w, display_h))

    font = cv2.FONT_HERSHEY_SIMPLEX

    # Pose overlay
    pose_text = f"x:{odometry.x:.3f}m  y:{odometry.y:.3f}m  theta:{np.rad2deg(odometry.theta):.1f}deg"
    cv2.putText(display, pose_text, (10, display_h - 10), font, 0.45, (0, 255, 0), 1)

    with status_lock:
        s = status.copy()

    label = s['maneuver'].upper()
    if s['pid_error_deg'] != 0.0:
        label += f"  err:{s['pid_error_deg']:.1f}deg"
    cv2.putText(display, label, (10, 22), font, 0.5, (0, 200, 255), 1)

    return display


generate_frames = make_frame_generator(lambda: camera, create_visualization, quality=70)


# ── Routes ────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template_string(
        HTML_TEMPLATE,
        title="ModCon — Simulation",
        subtitle="Odometry + PID Control — Godot Simulation",
        pid_kp=pid_module.K_P,
        pid_ki=pid_module.K_I,
        pid_kd=pid_module.K_D,
        v_0=robot_params['v_0'],
    )


@app.route('/video')
def video():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/status')
def get_status():
    with status_lock:
        s = status.copy()
    with path_lock:
        path = list(path_history)
    with pid_history_lock:
        pid_hist = list(pid_history[-200:])  # last ~20s
    return jsonify({
        'pose': {
            'x': round(odometry.x, 4),
            'y': round(odometry.y, 4),
            'theta_deg': round(np.rad2deg(odometry.theta), 2),
        },
        'maneuver': s['maneuver'],
        'message': s['message'],
        'pid_error_deg': s['pid_error_deg'],
        'omega': s['omega'],
        'path': path,
        'pid_history': pid_hist,
    })


@app.route('/maneuver', methods=['POST'])
def run_maneuver():
    data = request.json
    mtype = data.get('type', '')
    value = float(data.get('value', 0.5))

    if mtype == 'straight':
        distance = float(np.clip(value, 0.05, 5.0))
        start_maneuver(run_straight, distance)
        return jsonify({'status': 'ok', 'maneuver': 'straight', 'distance': distance})

    elif mtype == 'turn':
        start_maneuver(run_turn, value)
        return jsonify({'status': 'ok', 'maneuver': 'turn', 'degrees': value})

    elif mtype == 'square':
        side = float(np.clip(value, 0.1, 2.0))
        start_maneuver(run_square, side)
        return jsonify({'status': 'ok', 'maneuver': 'square', 'side': side})

    return jsonify({'status': 'error', 'message': 'Unknown maneuver'}), 400


@app.route('/stop', methods=['POST'])
def stop():
    stop_maneuver()
    return jsonify({'status': 'ok'})


@app.route('/reset_pose', methods=['POST'])
def reset_pose():
    stop_maneuver()
    odometry.reset_pose()
    with path_lock:
        path_history.clear()
    with pid_history_lock:
        pid_history.clear()
    with status_lock:
        status['message'] = 'Pose reset to (0, 0, 0°)'
    return jsonify({'status': 'ok'})


@app.route('/reset_sim', methods=['POST'])
def reset_sim():
    stop_maneuver()
    if wheels:
        wheels.reset_game()
    odometry.reset_pose()
    with path_lock:
        path_history.clear()
    with status_lock:
        status['message'] = 'Simulation reset'
        status['pid_error_deg'] = 0.0
        status['omega'] = 0.0
    return jsonify({'status': 'ok'})


@app.route('/update_pid', methods=['POST'])
def update_pid():
    data = request.json
    pid_module.K_P = float(data.get('K_P', pid_module.K_P))
    pid_module.K_I = float(data.get('K_I', pid_module.K_I))
    pid_module.K_D = float(data.get('K_D', pid_module.K_D))
    robot_params['v_0'] = float(data.get('v_0', robot_params['v_0']))

    try:
        with open(MODCON_CONFIG_FILE) as f:
            cfg = yaml.safe_load(f) or {}
        cfg['k_P'] = pid_module.K_P
        cfg['k_I'] = pid_module.K_I
        cfg['k_D'] = pid_module.K_D
        cfg['v_0'] = robot_params['v_0']
        with open(MODCON_CONFIG_FILE, 'w') as f:
            yaml.dump(cfg, f, default_flow_style=False, sort_keys=False)
    except Exception as e:
        print(f"Warning: could not save PID gains to config: {e}")

    return jsonify({'status': 'ok'})


@app.route('/save_calibration', methods=['POST'])
def save_calibration():
    if wheels:
        if wheels.save_calibration():
            return jsonify({'status': 'ok', 'message': 'Calibration saved!'})
        return jsonify({'status': 'error', 'message': 'Save failed'}), 500
    return jsonify({'status': 'error', 'message': 'Wheels not initialized'}), 503


# ── Entry Point ───────────────────────────────────────────────────────────
def main():
    global camera, wheels

    import argparse
    ap = argparse.ArgumentParser(description="Virtual ModCon Web Server for Godot")
    ap.add_argument("--port", type=int, default=5000, help="Web server port")
    ap.add_argument("--frame-port", type=int, default=5001, help="Godot camera port")
    ap.add_argument("--wheel-port", type=int, default=5002, help="Godot wheel port")
    ap.add_argument("--godot-host", type=str, default="localhost", help="Godot host")
    args = ap.parse_args()

    suppress_http_logs()
    print("=" * 60)
    print("MODCON (SIMULATION)")
    print("=" * 60)

    print("\n[1/2] Initializing wheels driver...")
    left_cfg = WheelPWMConfiguration()
    right_cfg = WheelPWMConfiguration()
    wheels = GodotWheelsDriver(
        left_cfg,
        right_cfg,
        godot_host=args.godot_host,
        godot_port=args.wheel_port,
    )
    wheels.trim = 0
    print(f"  Wheels: {args.godot_host}:{args.wheel_port}")

    print("\n[2/2] Initializing camera driver...")
    print(f"  Waiting for Godot to connect on port {args.frame_port}...")
    camera_cfg = GodotCameraConfig(host="0.0.0.0", port=args.frame_port)
    camera = GodotCameraDriver(godot_config=camera_cfg)
    camera.start()
    print("  Camera: connected!")

    web_port = find_available_port(args.port)
    if web_port != args.port:
        print(f"  Port {args.port} busy, using {web_port}")

    print("\n" + "=" * 60)
    print(f"Web Interface: http://localhost:{web_port}")
    print("=" * 60)
    print("\n1. Start Godot simulation")
    print("2. Open the web interface in your browser")
    print("3. Use the maneuver buttons to test your implementation")
    print("4. Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        app.run(host='127.0.0.1', port=web_port, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("\n\nShutting down...")
    finally:
        stop_maneuver()
        shutdown_cleanup(wheels, camera, stop_event)


if __name__ == "__main__":
    sys.exit(main())
