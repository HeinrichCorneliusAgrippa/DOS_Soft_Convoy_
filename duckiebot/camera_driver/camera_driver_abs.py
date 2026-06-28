from abc import ABC, abstractmethod
import numpy as np
import cv2
import os
import yaml
from typing import Tuple, Optional


class CameraDriverAbs(ABC):
    def __init__(self,config_file: str = None):
        if config_file is None:
            current_dir = os.path.dirname(__file__)
            config_file = os.path.join(current_dir, 'config/camera_config.yaml')

        self._load_config(config_file)
        self.config_file = config_file

        self._running = False
        self._frame_count = 0
        self._last_frame: Optional[np.ndarray] = None
        self._device = None
        self._consecutive_failures = 0


    @abstractmethod
    def _initialize_camera(self):
        pass

    @abstractmethod
    def _capture_frame(self) -> Tuple[bool, Optional[np.ndarray]]:
        pass

    @abstractmethod
    def _release_camera(self):
        pass


    def start(self):
        if self._running:
            print("[Camera] Already running")
            return

        self._initialize_camera()
        self._running = True
        print(f"Camera started successfully at {self.width}x{self.height} @ {self.framerate}fps")

    def stop(self):
        if not self._running:
            print("[Camera] Already stopped")
            return

        self._release_camera()
        self._running = False
        self._last_frame = None
        print("Camera stopped")

    def read(self) -> Tuple[bool, Optional[np.ndarray]]:
        if not self._running:
            # Only warn once, not every frame
            if not hasattr(self, '_warned_not_running'):
                print("[Camera] Warning: Camera not running, call start() first")
                self._warned_not_running = True
            return False, None

        success, frame = self._capture_frame()

        if success and frame is not None:
            self._last_frame = frame.copy()
            self._frame_count += 1
            self._consecutive_failures = 0
            return True, frame
        else:
            self._consecutive_failures += 1
            # Return last frame for brief hiccups (up to 30 failures ~ 0.3s)
            # After that return False so callers know the pipeline is dead
            if self._last_frame is not None and self._consecutive_failures < 30:
                return True, self._last_frame
            return False, None

    def read_jpeg(self) -> Tuple[bool, Optional[bytes]]:
        success, frame = self.read()

        if not success or frame is None:
            return False, None

        # Encode to JPEG
        ret, jpeg = cv2.imencode('.jpg', frame)
        if ret:
            return True, jpeg.tobytes()
        else:
            return False, None



    def _load_config(self, filepath: str):
        try:
            with open(filepath, 'r') as f:
                config = yaml.safe_load(f)

            res = config.get('resolution', {})
            self.width = res.get('width', 640)
            self.height = res.get('height', 480)

            self.framerate = config.get('framerate', 30)
            self.sensor_mode = config.get('sensor_mode', 0)
            self.use_hw_acceleration = config.get('use_hw_acceleration', True)

            self.maker = config.get('maker', 'Unknown')
            self.model = config.get('model', 'Unknown')
            self.fov = config.get('fov', 160)
            self.exposure_mode = config.get('exposure_mode', 'sports')
            # White balance (fixes the IMX219 green cast). nvarguscamerasrc wbmode:
            #   0=off 1=auto 2=incandescent 3=fluorescent 4=daylight 5=cloudy 6=twilight
            # If auto (1) still looks green, try 3 (lab fluorescent) or 4 (daylight).
            self.wbmode = config.get('wbmode', 1)
            self.saturation = config.get('saturation', 1.0)

            print(f"[CameraDriver] Loaded config from {filepath}")

        except FileNotFoundError:
            print(f"[CameraDriver] Warning: Config not found: {filepath}")


    @property
    def resolution(self) -> Tuple[int, int]:
          return (self.width, self.height)


    def framerate(self) -> int:
        return self._framerate

    @property
    def is_active(self) -> bool:
        return self._running

    @property
    def frame_count(self) -> int:
        return self._frame_count

    def __del__(self):
        if self._running:
            self.stop()
