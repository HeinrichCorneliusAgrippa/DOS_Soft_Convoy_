from dataclasses import dataclass, field
from enum import Enum
from math import fabs, floor, pi
import json
import os
import select
import socket
import struct
import threading
import time
from typing import Optional, Callable

import yaml

from .wheels_driver_abs import WheelsDriverAbs, WheelPWMConfiguration

uint8 = int
float1 = float


class MotorDirection(Enum):
    RELEASE = 0
    FORWARD = 1
    BACKWARD = -1


@dataclass
class GodotTransportConfig:
    host: str = "localhost"
    port: int = 5002
    reconnect_interval_s: float = 1.0
    socket_timeout_s: float = 0.2


@dataclass
class GameState:
    game_over: bool = False
    survival_time: float = 0.0
    distance_traveled: float = 0.0
    distance_from_start: float = 0.0
    collision_duck: str = ""
    # Robot pose, pushed by newer Godot builds inside the periodic "state"
    # message; None until the first one arrives (or with older builds).
    pose_x: Optional[float] = None
    pose_z: Optional[float] = None
    heading_rad: Optional[float] = None
    # NPC leader pose (convoy follow scene only).
    npc_x: Optional[float] = None
    npc_z: Optional[float] = None


class GodotWheelTransport:

    def __init__(self, cfg: GodotTransportConfig):
        self.cfg = cfg
        self._sock: Optional[socket.socket] = None
        self._last_connect_attempt: float = 0.0
        self._recv_buffer: bytes = b""
        # The agent thread (send_wheels) and server threads (is_game_over for
        # pose/game polling) share this socket; serialize all I/O on it.
        self._io_lock = threading.Lock()

        self.game_state = GameState()
        self._on_game_over: Optional[Callable[[GameState], None]] = None

    def set_game_over_callback(self, callback: Callable[[GameState], None]) -> None:
        self._on_game_over = callback

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
        self._sock = None
        self._recv_buffer = b""

    def _ensure_connected(self) -> bool:
        if self._sock is not None:
            return True

        now = time.time()
        if now - self._last_connect_attempt < self.cfg.reconnect_interval_s:
            return False

        self._last_connect_attempt = now
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.settimeout(self.cfg.socket_timeout_s)
            s.connect((self.cfg.host, self.cfg.port))
            s.setblocking(False)  # Non-blocking for reading
            self._sock = s
            self.game_state = GameState()  # Reset game state on new connection
            print(f"[GodotWheelTransport] Connected to {self.cfg.host}:{self.cfg.port}")
            return True
        except Exception as e:
            self.close()
            print(f"[GodotWheelTransport] Connect failed ({self.cfg.host}:{self.cfg.port}): {e}")
            return False

    def _check_incoming(self) -> None:
        if self._sock is None:
            return

        try:
            # Use select for non-blocking check
            readable, _, _ = select.select([self._sock], [], [], 0)
            if not readable:
                return

            # Read available data
            chunk = self._sock.recv(4096)
            if not chunk:
                # Peer closed the connection: drop the socket so
                # _ensure_connected can reconnect (a read-only consumer
                # would otherwise hold the dead socket forever).
                self.close()
                return
            self._recv_buffer += chunk

            # Process complete messages
            while len(self._recv_buffer) >= 4:
                # Read length (big-endian)
                msg_len = struct.unpack("!I", self._recv_buffer[:4])[0]

                if msg_len <= 0 or msg_len > 4096:
                    # Bad message, clear buffer
                    self._recv_buffer = b""
                    return

                if len(self._recv_buffer) < 4 + msg_len:
                    # Incomplete message, wait for more data
                    return

                # Extract message
                payload = self._recv_buffer[4:4 + msg_len]
                self._recv_buffer = self._recv_buffer[4 + msg_len:]

                self._handle_message(payload)

        except BlockingIOError:
            pass
        except Exception:
            pass

    def _handle_message(self, payload: bytes) -> None:
        try:
            msg = json.loads(payload.decode("utf-8"))
            msg_type = msg.get("type", "")

            if msg_type == "game_over":
                self.game_state = GameState(
                    game_over=True,
                    survival_time=float(msg.get("survival_time", 0)),
                    distance_traveled=float(msg.get("distance_traveled", 0)),
                    distance_from_start=float(msg.get("distance_from_start", 0)),
                )
                print(f"\n{'='*50}")
                print("[GAME OVER] Duck collision detected!")
                print(f"  Survival time: {self.game_state.survival_time:.2f} seconds")
                print(f"  Distance traveled: {self.game_state.distance_traveled:.2f} meters")
                print(f"  Distance from start: {self.game_state.distance_from_start:.2f} meters")
                print(f"{'='*50}\n")

                if self._on_game_over:
                    self._on_game_over(self.game_state)

            elif msg_type == "state":
                def _opt_float(key):
                    v = msg.get(key)
                    return None if v is None else float(v)

                self.game_state = GameState(
                    game_over=bool(msg.get("game_over", False)),
                    survival_time=float(msg.get("survival_time", 0)),
                    distance_traveled=float(msg.get("total_distance", 0)),
                    distance_from_start=float(msg.get("distance_from_start", 0)),
                    collision_duck=str(msg.get("collision_duck", "")),
                    pose_x=_opt_float("pose_x"),
                    pose_z=_opt_float("pose_z"),
                    heading_rad=_opt_float("heading_rad"),
                    npc_x=_opt_float("npc_x"),
                    npc_z=_opt_float("npc_z"),
                )

        except Exception as e:
            print(f"[GodotWheelTransport] Error handling message: {e}")

    def send_wheels(self, left: float, right: float) -> None:
        with self._io_lock:
            # Check for incoming messages first
            self._check_incoming()

            if not self._ensure_connected():
                return

            msg = {"type": "wheels", "left": float(left), "right": float(right), "ts": float(time.time())}
            payload = json.dumps(msg).encode("utf-8")
            header = struct.pack("!I", len(payload))

            try:
                assert self._sock is not None
                self._sock.sendall(header + payload)
            except Exception as e:
                print(f"[GodotWheelTransport] Send failed: {e}")
                self.close()

    def send_reset(self) -> None:
        with self._io_lock:
            if not self._ensure_connected():
                return

            msg = {"type": "reset"}
            payload = json.dumps(msg).encode("utf-8")
            header = struct.pack("!I", len(payload))

            try:
                assert self._sock is not None
                self._sock.sendall(header + payload)
                self.game_state = GameState()  # Reset local state
                print("[GodotWheelTransport] Sent reset command")
            except Exception as e:
                print(f"[GodotWheelTransport] Reset send failed: {e}")
                self.close()

    def send_remove_objects(self, name_filter: str) -> None:
        with self._io_lock:
            if not self._ensure_connected():
                return

            msg = {"type": "remove_objects", "filter": name_filter}
            payload = json.dumps(msg).encode("utf-8")
            header = struct.pack("!I", len(payload))

            try:
                assert self._sock is not None
                self._sock.sendall(header + payload)
                print(f"[GodotWheelTransport] Sent remove_objects filter={name_filter!r}")
            except Exception as e:
                print(f"[GodotWheelTransport] remove_objects send failed: {e}")
                self.close()

    def send_change_scene(self, scene_path: str) -> None:
        with self._io_lock:
            if not self._ensure_connected():
                return

            msg = {"type": "change_scene", "scene": scene_path}
            payload = json.dumps(msg).encode("utf-8")
            header = struct.pack("!I", len(payload))

            try:
                assert self._sock is not None
                self._sock.sendall(header + payload)
                print(f"[GodotWheelTransport] Sent change_scene path={scene_path!r}")
            except Exception as e:
                print(f"[GodotWheelTransport] change_scene send failed: {e}")
                self.close()

    def is_game_over(self) -> bool:
        with self._io_lock:
            self._check_incoming()
            if self._sock is None:
                self._ensure_connected()
        return self.game_state.game_over

    def clear_state(self) -> None:
        """Drain anything Godot already pushed, then forget it. Used after a
        game reset so state generated before the respawn (old pose, stale
        game_over) can never be served afterwards."""
        with self._io_lock:
            self._check_incoming()
            self.game_state = GameState()


class GodotWheelsDriver(WheelsDriverAbs):
    """Behaves like DaguWheelsDriver but sends executed PWM to Godot."""

    def __init__(
        self,
        left_config: WheelPWMConfiguration,
        right_config: WheelPWMConfiguration,
        calibration_file: Optional[str] = None,
        godot_host: str = "localhost",
        godot_port: int = 5002,
    ):
        super().__init__(left_config, right_config)

        if calibration_file is None:
            current_dir = os.path.dirname(__file__)
            calibration_file = os.path.join(current_dir, "../../config/modcon_config.yaml")

        self._load_calibration(calibration_file)
        self.calibration_file = calibration_file

        self.transport = GodotWheelTransport(GodotTransportConfig(host=godot_host, port=godot_port))

        this = self.__class__.__name__
        print(f"[{this}] Calibration: gain={self.gain}, trim={self.trim}")
        print(f"[{this}] Physical: R={self.radius}m, baseline={self.baseline}m")
        print(f"[{this}] Limits: v_max={self.v_max} m/s, omega_max={self.omega_max} rad/s")
        print(f"[{this}] Godot transport: {godot_host}:{godot_port}")

        self._executed_left: float1 = 0.0
        self._executed_right: float1 = 0.0
        self.encoders = None  # no GPIO encoders in sim; agent uses game_state.distance_traveled
        self.set_wheels_speed(0.0, 0.0)

    @property
    def left_pwm(self) -> float1:
        return self._executed_left

    @property
    def right_pwm(self) -> float1:
        return self._executed_right

    @property
    def game_state(self) -> GameState:
        return self.transport.game_state

    def is_game_over(self) -> bool:
        """Check if game is over (duck collision)."""
        return self.transport.is_game_over()

    def reset_game(self) -> None:
        self.transport.send_reset()

    def clear_state(self) -> None:
        self.transport.clear_state()

    def remove_objects(self, name_filter: str) -> None:
        self.transport.send_remove_objects(name_filter)

    def change_scene(self, scene_path: str) -> None:
        self.transport.send_change_scene(scene_path)

    def set_game_over_callback(self, callback: Callable[[GameState], None]) -> None:
        self.transport.set_game_over_callback(callback)

    def set_wheels_speed(self, left: float, right: float):
        # Apply calibration (gain and trim)
        left_calibrated = left * (self.gain - self.trim)
        right_calibrated = right * (self.gain + self.trim)

        # Clamp to [-1, 1] after calibration
        left_calibrated = max(-1.0, min(1.0, left_calibrated))
        right_calibrated = max(-1.0, min(1.0, right_calibrated))

        pwml: uint8 = self._pwm_value(left_calibrated, self.left_config)
        pwmr: uint8 = self._pwm_value(right_calibrated, self.right_config)

        leftMotorMode = MotorDirection.RELEASE
        rightMotorMode = MotorDirection.RELEASE

        # direction + deadzone (match your real driver)
        if fabs(left) < self.left_config.deadzone:
            pwml = 0
        elif left > 0:
            leftMotorMode = MotorDirection.FORWARD
        elif left < 0:
            leftMotorMode = MotorDirection.BACKWARD

        if fabs(right) < self.right_config.deadzone:
            pwmr = 0
        elif right > 0:
            rightMotorMode = MotorDirection.FORWARD
        elif right < 0:
            rightMotorMode = MotorDirection.BACKWARD

        # executed PWM in [-1, 1]
        self._executed_left = (pwml * leftMotorMode.value) / 255.0
        self._executed_right = (pwmr * rightMotorMode.value) / 255.0

        if not self.pretend:
            self.transport.send_wheels(self._executed_left, self._executed_right)

    def set_velocity(self, v: float, omega: float):
        v = max(-self.v_max, min(self.v_max, v))
        omega = max(-self.omega_max, min(self.omega_max, omega))

        v_left = (v - omega * self.baseline / 2.0) / self.radius
        v_right = (v + omega * self.baseline / 2.0) / self.radius

        wheel_max = self.v_max / self.radius  # max wheel angular velocity [rad/s]
        left_normalized = max(-1.0, min(1.0, v_left / wheel_max))
        right_normalized = max(-1.0, min(1.0, v_right / wheel_max))

        self.set_wheels_speed(left_normalized, right_normalized)

    @staticmethod
    def _pwm_value(v: float, wheel_config: WheelPWMConfiguration) -> uint8:
        pwm: uint8 = 0
        if fabs(v) > wheel_config.deadzone:
            effective_max = wheel_config.pwm_min + int((wheel_config.pwm_max - wheel_config.pwm_min) * wheel_config.power_limit)
            pwm = int(floor(fabs(v) * (effective_max - wheel_config.pwm_min) + wheel_config.pwm_min))
            return min(pwm, effective_max)
        return pwm

    def _load_calibration(self, filepath: str):
        try:
            with open(filepath, "r") as f:
                calib = yaml.safe_load(f) or {}

            self.gain = float(calib.get("gain", 1.0))
            self.trim = float(calib.get("trim", 0.0))
            self.baseline = float(calib.get("baseline", 0.1))
            self.radius = float(calib.get("radius", 0.0318))
            self.v_max = float(calib.get("v_max", 1.0))
            self.omega_max = float(calib.get("omega_max", 8.0))
            self.k = float(calib.get("k", 27.0))
            self.limit = float(calib.get("limit", 1.0))
            power_limit = float(calib.get("power_limit", 1.0))
            self.left_config.power_limit  = power_limit
            self.right_config.power_limit = power_limit

            print(f"[GodotWheelsDriver] Loaded calibration from {filepath}")

        except FileNotFoundError:
            print(f"[GodotWheelsDriver] Warning: Calibration file not found: {filepath}")
            print("[GodotWheelsDriver] Using default values")
            self.gain = 1.0
            self.trim = 0.0
            self.baseline = 0.1
            self.radius = 0.0318
            self.v_max = 1.0
            self.omega_max = 8.0
            self.k = 27.0
            self.limit = 1.0

    def save_calibration(self, filepath: str = None):
        if filepath is None:
            filepath = self.calibration_file

        try:
            calib = {
                'gain': float(self.gain),
                'trim': float(self.trim),
                'baseline': float(self.baseline),
                'radius': float(self.radius),
                'v_max': float(self.v_max),
                'omega_max': float(self.omega_max),
                'k': float(self.k),
                'limit': float(self.limit)
            }

            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            with open(filepath, 'w') as f:
                yaml.dump(calib, f, default_flow_style=False)

            print(f"[GodotWheelsDriver] Saved calibration to {filepath}")
            return True
        except Exception as e:
            print(f"[GodotWheelsDriver] Error saving calibration: {e}")
            return False

    def __del__(self):
        try:
            self.transport.close()
        except Exception:
            pass
