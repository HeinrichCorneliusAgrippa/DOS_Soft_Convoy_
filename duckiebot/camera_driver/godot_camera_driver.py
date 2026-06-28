"""
Godot-backed camera driver.

Receives frames from Godot CameraTcpStreamer via TCP.

Protocol:
    4 bytes  : ASCII magic b'GIMG' or b'GPNG'
    4 bytes  : uint32_be payload length N (big-endian / network byte order)
    N bytes  : encoded image (jpeg/png)
"""

import socket
import struct
import threading
from dataclasses import dataclass
from typing import Optional, Tuple, Callable

import cv2
import numpy as np

from .camera_driver_abs import CameraDriverAbs


@dataclass
class GodotCameraConfig:
    host: str = "0.0.0.0"
    port: int = 5001
    accept_timeout_s: float = 60.0  # Wait up to 60s for Godot to connect
    recv_timeout_s: float = 5.0
    max_frame_bytes: int = 5_000_000  # safety limit


class GodotCameraDriver(CameraDriverAbs):
    """Camera driver that receives frames from Godot via TCP.

    Runs a background thread to continuously drain the socket so the OS
    buffer never fills and Godot's send never blocks/dies.  _capture_frame()
    just returns the most recently decoded frame instantly.
    """

    def __init__(self, config_file: str = None, godot_config: GodotCameraConfig = None):
        super().__init__(config_file)

        self.godot_cfg = godot_config or GodotCameraConfig()
        self._srv: Optional[socket.socket] = None
        self._conn: Optional[socket.socket] = None
        self._addr = None

        self._latest_frame: Optional[np.ndarray] = None
        self._frame_condition = threading.Condition()
        self._recv_thread: Optional[threading.Thread] = None
        self._recv_running = False

    def _initialize_camera(self):
        self._srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind((self.godot_cfg.host, self.godot_cfg.port))
        self._srv.listen(1)
        self._srv.settimeout(self.godot_cfg.accept_timeout_s)
        print(f"[GodotCameraDriver] Listening on {self.godot_cfg.host}:{self.godot_cfg.port}")

        self._conn, self._addr = self._srv.accept()
        self._conn.settimeout(self.godot_cfg.recv_timeout_s)
        print(f"[GodotCameraDriver] Connected by {self._addr}")

        self._recv_running = True
        self._recv_thread = threading.Thread(target=self._recv_loop, daemon=True, name="godot-cam-recv")
        self._recv_thread.start()

    def _recv_loop(self):
        """Background thread: drain socket, store latest frame, re-accept on disconnect."""
        while self._recv_running:
            # Re-accept if connection dropped (e.g. Godot scene change)
            if self._conn is None:
                try:
                    self._srv.settimeout(2.0)
                    self._conn, self._addr = self._srv.accept()
                    self._conn.settimeout(self.godot_cfg.recv_timeout_s)
                    print(f"[GodotCameraDriver] Reconnected by {self._addr}")
                except socket.timeout:
                    continue
                except Exception as e:
                    print(f"[GodotCameraDriver] Accept error: {e}")
                    break

            try:
                magic = self._recv_exact(4)
                if magic not in (b"GIMG", b"GPNG"):
                    if not self._resync_to_magic():
                        raise ConnectionError("Lost sync")

                length_bytes = self._recv_exact(4)
                n = struct.unpack("<I", length_bytes)[0]

                if n <= 0 or n > self.godot_cfg.max_frame_bytes:
                    print(f"[GodotCameraDriver] Invalid frame length: {n}, skipping")
                    continue

                payload = self._recv_exact(n)
                arr = np.frombuffer(payload, dtype=np.uint8)
                bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)

                if bgr is not None:
                    with self._frame_condition:
                        self._latest_frame = bgr
                        self._frame_condition.notify_all()

            except (ConnectionError, socket.timeout) as e:
                print(f"[GodotCameraDriver] Disconnected ({e}), waiting for reconnect...")
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
            except Exception as e:
                print(f"[GodotCameraDriver] Recv loop error: {e}")
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

        with self._frame_condition:
            self._recv_running = False
            self._frame_condition.notify_all()
        print("[GodotCameraDriver] Recv loop exited")

    def _release_camera(self):
        with self._frame_condition:
            self._recv_running = False
            self._frame_condition.notify_all()

        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None

        if self._srv:
            try:
                self._srv.close()
            except Exception:
                pass
        self._srv = None

        if self._recv_thread and self._recv_thread.is_alive():
            self._recv_thread.join(timeout=2.0)
        self._recv_thread = None

    def _capture_frame(self) -> Tuple[bool, Optional[np.ndarray]]:
        """Block until a new frame arrives, then return it."""
        with self._frame_condition:
            if not self._recv_running and self._latest_frame is None:
                return False, None
            self._frame_condition.wait(timeout=1.0)
            if self._latest_frame is None:
                return False, None
            return True, self._latest_frame.copy()

    def _recv_exact(self, n: int) -> bytes:
        assert self._conn is not None
        buf = bytearray()
        while len(buf) < n:
            chunk = self._conn.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("Socket closed by peer")
            buf.extend(chunk)
        return bytes(buf)

    def _resync_to_magic(self) -> bool:
        """Scan stream until we find b'GIMG' or b'GPNG'. This recovers from desync."""
        assert self._conn is not None
        valid_magics = (b"GIMG", b"GPNG")
        window = bytearray(4)

        try:
            initial = self._recv_exact(4)
            window[:] = initial
        except ConnectionError:
            return False

        if bytes(window) in valid_magics:
            return True

        while True:
            try:
                b = self._conn.recv(1)
                if not b:
                    return False
                window.pop(0)
                window.append(b[0])
                if bytes(window) in valid_magics:
                    return True
            except (ConnectionError, socket.timeout):
                return False

    def read_rgb(self) -> Tuple[bool, Optional[np.ndarray]]:
        """Read frame as RGB (convenience method for Braitenberg code)."""
        success, bgr = self.read()
        if success and bgr is not None:
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            return True, rgb
        return False, None

    def recv_frame_rgb(self) -> np.ndarray:
        """Legacy compat: auto-starts if needed, returns RGB, raises on failure."""
        if not self._running:
            self.start()

        success, rgb = self.read_rgb()
        if not success or rgb is None:
            raise ConnectionError("Failed to receive frame")
        return rgb