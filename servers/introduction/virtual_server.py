import sys
import os
import threading
import time
import argparse

script_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.join(script_dir, '..', '..')
sys.path.insert(0, project_root)

from flask import Flask, Response, render_template_string, request, jsonify
import cv2
import numpy as np

from servers.templates.introduction import INTRODUCTION_TEMPLATE as HTML_TEMPLATE

from duckiebot.wheel_driver.godot_wheels_driver import GodotWheelsDriver
from duckiebot.wheel_driver.wheels_driver_abs import WheelPWMConfiguration
from duckiebot.camera_driver.godot_camera_driver import GodotCameraDriver, GodotCameraConfig
from launcher.ports import find_available_port
from servers.common import make_frame_generator, shutdown_cleanup, suppress_http_logs
from tasks.introduction.packages import manual_drive, led_control

app = Flask(__name__)

camera = None
wheels = None
keys_pressed = {'up': False, 'down': False, 'left': False, 'right': False}
keys_lock = threading.Lock()
_keys_last_update = time.time()
current_speeds = {'left': 0.0, 'right': 0.0}
stop_event = threading.Event()
student_code_works = True


def control_loop():
    """Background thread that reads key state and drives wheels at 20Hz."""
    global keys_pressed, current_speeds, student_code_works, _keys_last_update

    print("[ControlLoop] Starting...")

    while not stop_event.is_set():
        try:
            # Server-side key timeout: clear keys if no update in 500ms
            if time.time() - _keys_last_update > 0.5:
                with keys_lock:
                    keys_pressed = {'up': False, 'down': False, 'left': False, 'right': False}

            with keys_lock:
                keys_copy = keys_pressed.copy()

            try:
                left, right = manual_drive.get_motor_speeds(keys_copy)
                student_code_works = True
            except Exception as e:
                print(f"[ControlLoop] Student code error: {e}")
                left, right = 0.0, 0.0
                student_code_works = False

            current_speeds['left'] = left
            current_speeds['right'] = right

            if wheels:
                wheels.set_wheels_speed(left, right)

            time.sleep(0.05)  # 20 Hz

        except Exception as e:
            print(f"[ControlLoop] Error: {e}")
            time.sleep(0.1)

    print("[ControlLoop] Stopped")


def create_visualization(frame):
    """Create camera view with key indicator and speed overlay."""
    global current_speeds, keys_pressed, student_code_works

    if frame is None:
        placeholder = np.zeros((240, 640, 3), dtype=np.uint8)
        cv2.putText(placeholder, "Waiting for Godot...", (200, 120),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (100, 100, 100), 2)
        return placeholder

    # Convert RGB to BGR for OpenCV
    display = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    h, w = display.shape[:2]

    # Scale to reasonable display size
    display_w = 640
    display_h = int(h * display_w / w)
    display = cv2.resize(display, (display_w, display_h))

    font = cv2.FONT_HERSHEY_SIMPLEX

    # Draw speed readout at bottom
    speed_text = f"L: {current_speeds['left']:+.2f}  R: {current_speeds['right']:+.2f}"
    cv2.putText(display, speed_text, (10, display_h - 10), font, 0.6, (0, 255, 0), 2)

    # Draw key indicator in bottom-right
    with keys_lock:
        kc = keys_pressed.copy()

    key_size = 30
    gap = 4
    base_x = display_w - 3 * (key_size + gap) - 10
    base_y = display_h - 2 * (key_size + gap) - 10

    key_positions = {
        'up': (base_x + key_size + gap, base_y),
        'left': (base_x, base_y + key_size + gap),
        'down': (base_x + key_size + gap, base_y + key_size + gap),
        'right': (base_x + 2 * (key_size + gap), base_y + key_size + gap),
    }
    key_labels = {'up': '^', 'down': 'v', 'left': '<', 'right': '>'}

    for key, (kx, ky) in key_positions.items():
        color = (0, 200, 0) if kc.get(key, False) else (60, 60, 60)
        cv2.rectangle(display, (kx, ky), (kx + key_size, ky + key_size), color, -1)
        cv2.rectangle(display, (kx, ky), (kx + key_size, ky + key_size), (100, 100, 100), 1)
        cv2.putText(display, key_labels[key], (kx + 8, ky + 22), font, 0.6, (255, 255, 255), 2)

    return display


generate_frames = make_frame_generator(lambda: camera, create_visualization, quality=70)


@app.route('/')
def index():
    return render_template_string(
        HTML_TEMPLATE,
        title="Introduction — Keyboard Control",
        subtitle="Drive your Duckiebot with arrow keys or WASD",
    )


@app.route('/video')
def video():
    return Response(generate_frames(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/keys', methods=['POST'])
def update_keys():
    global keys_pressed, _keys_last_update
    data = request.json
    with keys_lock:
        keys_pressed = {
            'up': bool(data.get('up', False)),
            'down': bool(data.get('down', False)),
            'left': bool(data.get('left', False)),
            'right': bool(data.get('right', False)),
        }
    _keys_last_update = time.time()
    return jsonify({'status': 'ok',
                    'left': current_speeds['left'],
                    'right': current_speeds['right']})


@app.route('/speeds')
def get_speeds():
    return jsonify(current_speeds)


@app.route('/wheels', methods=['POST'])
def set_wheels():
    """Directly set wheel speeds. Body: {left: float, right: float}"""
    data = request.json
    left = max(-1.0, min(1.0, float(data.get('left', 0.0))))
    right = max(-1.0, min(1.0, float(data.get('right', 0.0))))
    if wheels:
        wheels.set_wheels_speed(left, right)
    return jsonify({'status': 'ok', 'left': left, 'right': right})


@app.route('/snapshot')
def snapshot():
    if camera is None:
        return jsonify({'status': 'error', 'message': 'Camera not ready'}), 503

    success, frame = camera.read_rgb()
    if not success or frame is None:
        return jsonify({'status': 'error', 'message': 'No frame available'}), 503

    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
    ret, jpeg = cv2.imencode('.jpg', frame_bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ret:
        return jsonify({'status': 'error', 'message': 'Encode failed'}), 500

    return Response(jpeg.tobytes(), mimetype='image/jpeg')


# Stub LED endpoints for API compatibility with real server
_virtual_led_states = {0: [0,0,0], 2: [0,0,0], 3: [0,0,0], 4: [0,0,0]}

@app.route('/leds', methods=['POST'])
def set_led():
    data = request.json
    led_index = int(data.get('led', 0))
    color = [max(0.0, min(1.0, float(c))) for c in data.get('color', [0,0,0])]
    if led_index in _virtual_led_states:
        _virtual_led_states[led_index] = color
    print(f"[VirtualLED] LED {led_index} = {color}")
    return jsonify({'status': 'ok', 'led': led_index, 'color': color})

@app.route('/leds/all', methods=['POST'])
def set_all_leds():
    color = [max(0.0, min(1.0, float(c))) for c in request.json.get('color', [0,0,0])]
    for idx in (0, 2, 3, 4):
        _virtual_led_states[idx] = color[:]
    print(f"[VirtualLED] All LEDs = {color}")
    return jsonify({'status': 'ok', 'color': color})

@app.route('/leds/off', methods=['POST'])
def leds_off():
    for idx in (0, 2, 3, 4):
        _virtual_led_states[idx] = [0, 0, 0]
    print("[VirtualLED] All LEDs off")
    return jsonify({'status': 'ok'})

@app.route('/leds/state')
def get_led_state():
    return jsonify(_virtual_led_states)


def main():
    global camera, wheels, stop_event

    ap = argparse.ArgumentParser(description="Virtual Introduction Server")
    ap.add_argument("--port", type=int, default=5000, help="Web server port")
    ap.add_argument("--frame-port", type=int, default=5001, help="Godot camera port")
    ap.add_argument("--wheel-port", type=int, default=5002, help="Godot wheel port")
    ap.add_argument("--godot-host", type=str, default="localhost", help="Godot host")
    args = ap.parse_args()

    suppress_http_logs()
    print("=" * 60)
    print("INTRODUCTION (SIMULATION)")
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
    print(f"  Camera: connected!")

    stop_event.clear()
    control_thread = threading.Thread(target=control_loop, daemon=True)
    control_thread.start()

    web_port = find_available_port(args.port)
    if web_port != args.port:
        print(f"  Port {args.port} busy, using {web_port}")

    print("\n" + "=" * 60)
    print(f"Web Interface: http://localhost:{web_port}")
    print("=" * 60)
    print("\n1. Start Godot simulation")
    print("2. Open the web interface in your browser")
    print("3. Use arrow keys or WASD to drive")
    print("4. Press Ctrl+C to stop")
    print("=" * 60 + "\n")

    try:
        app.run(host='127.0.0.1', port=web_port, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("\n\nShutting down...")
    finally:
        shutdown_cleanup(wheels, camera, stop_event)


if __name__ == "__main__":
    sys.exit(main())
