"""AprilTag traffic-sign detection (lead bot only).

Lifted out of the original monolithic perception.py so the lead can detect
STOP/SLOW signs without pulling in the YOLO object-detection stack. Detection
uses cv2.aruco (all AprilTag families ship with OpenCV >= 4.7).
"""
from typing import List, Optional

import cv2
import numpy as np

from tasks.project.packages.world_model import SignKind, SignObs

# Map our config's family name to the OpenCV aruco predefined-dictionary attr.
_APRILTAG_DICTS = {
    "tag36h11": "DICT_APRILTAG_36h11",
    "tag36h10": "DICT_APRILTAG_36h10",
    "tag25h9":  "DICT_APRILTAG_25h9",
    "tag16h5":  "DICT_APRILTAG_16h5",
}


class SignDetector:
    def __init__(self, cfg: Optional[dict] = None):
        cfg = cfg or {}
        self.family = str(cfg.get("apriltag_family", "tag36h11"))
        self.stop_ids = {int(i) for i in (cfg.get("apriltag_stop_ids") or [])}
        self.slow_ids = {int(i) for i in (cfg.get("apriltag_slow_ids") or [])}
        # Ignore tags smaller than this (px^2) so a far-away sign doesn't arm the
        # intersection logic too early. 0 = react to any detected tag.
        self.min_area_px = float(cfg.get("apriltag_min_area_px", 0))

        self._aruco = None
        self._detector = self._make_detector(self.family)
        self.last_tag_ids: List[int] = []

    # --- detector construction -------------------------------------------------
    def _make_detector(self, family: str):
        try:
            import cv2.aruco as aruco
        except Exception as e:  # pragma: no cover - depends on the cv2 build
            print(f"[lead] cv2.aruco unavailable ({e}); AprilTag sign detection "
                  f"DISABLED. Install opencv-contrib-python.")
            return None

        self._aruco = aruco
        dict_name = _APRILTAG_DICTS.get(str(family).lower(), "DICT_APRILTAG_36h11")
        if not hasattr(aruco, dict_name):
            print(f"[lead] AprilTag family '{family}' unsupported; using tag36h11.")
            dict_name = "DICT_APRILTAG_36h11"
        dict_id = getattr(aruco, dict_name)

        if hasattr(aruco, "getPredefinedDictionary"):
            ad = aruco.getPredefinedDictionary(dict_id)
        else:  # very old API
            ad = aruco.Dictionary_get(dict_id)

        if hasattr(aruco, "ArucoDetector"):  # OpenCV >= 4.7
            return ("obj", aruco.ArucoDetector(ad, aruco.DetectorParameters()))
        return ("fn", (ad, aruco.DetectorParameters_create()))

    # --- detection -------------------------------------------------------------
    def _detect_tags(self, gray: np.ndarray):
        if self._detector is None:
            return [], []
        mode, det = self._detector
        try:
            if mode == "obj":
                corners, ids, _ = det.detectMarkers(gray)
            else:
                ad, params = det
                corners, ids, _ = self._aruco.detectMarkers(gray, ad, parameters=params)
        except cv2.error:
            return [], []
        if ids is None:
            return [], []
        return corners, [int(i) for i in ids.flatten()]

    def _kind(self, tid: int) -> Optional[SignKind]:
        if tid in self.stop_ids:
            return "STOP"
        if tid in self.slow_ids:
            return "SLOW"
        return None

    def detect(self, gray: np.ndarray) -> List[SignObs]:
        corners, ids = self._detect_tags(gray)
        self.last_tag_ids = ids  # surfaced in debug so unmapped tags stay visible
        out: List[SignObs] = []
        for pts4, tid in zip(corners, ids):
            kind = self._kind(tid)
            if kind is None:
                continue
            pts = np.asarray(pts4).reshape(-1, 2)
            x1, y1 = pts.min(axis=0)
            x2, y2 = pts.max(axis=0)
            if (x2 - x1) * (y2 - y1) < self.min_area_px:
                continue
            out.append(SignObs(kind=kind, bbox=(int(x1), int(y1), int(x2), int(y2)), score=1.0))
        return out
