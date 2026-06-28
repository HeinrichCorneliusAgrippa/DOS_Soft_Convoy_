import cv2
import numpy as np
import os
import yaml
from typing import Tuple, Optional
from .camera_driver_abs import CameraDriverAbs


class CameraDriver(CameraDriverAbs):
    def _build_gstreamer_pipeline(self) -> str:
        # nvarguscamerasrc only outputs native sensor modes (1280x720, 1640x1232, etc.)
        # Requesting a non-native size (e.g. 640x480) in the source caps causes caps
        # negotiation failure. Let the source pick a native mode by framerate, then
        # scale to the desired output size via nvvidconv.
        pipeline = (
          f"nvarguscamerasrc wbmode={self.wbmode} saturation={self.saturation} ! "
          f"video/x-raw(memory:NVMM), format=NV12, framerate={self.framerate}/1 ! "
          f"nvvidconv ! "
          f"video/x-raw, width={self.width}, height={self.height}, format=BGRx ! "
          f"videoconvert ! "
          f"appsink drop=true sync=false"
        )


        return pipeline



    def _initialize_camera(self):
        pipeline = self._build_gstreamer_pipeline()

        print(f"[JetsonCamera] Pipeline: {pipeline}")

        self._device = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)

        if not self._device.isOpened():
            raise RuntimeError("Failed to open camera")

        # isOpened() can return True even when GStreamer fails internally.
        # nvarguscamerasrc needs a moment to warm up, so retry a few times.
        import time
        for _ in range(10):
            ret, _ = self._device.read()
            if ret:
                break
            time.sleep(0.3)
        else:
            self._device.release()
            self._device = None
            raise RuntimeError("Camera opened but returned no frames after warm-up — check nvargus-daemon and camera connection")

        actual_w = int(self._device.get(cv2.CAP_PROP_FRAME_WIDTH))
        actual_h = int(self._device.get(cv2.CAP_PROP_FRAME_HEIGHT))

    def _capture_frame(self) -> Tuple[bool, Optional[np.ndarray]]:
        if self._device is None:
            return False, None

        ret, frame = self._device.read()
        return (True, frame) if ret else (False, None)

    def _release_camera(self):
        if self._device is not None:
            self._device.release()
            self._device = None
