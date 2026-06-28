"""Red stop-line detector for the lead bot (intersection cue).

Duckietown paints a red line across the lane before each intersection. We look
for a *wide, short* red component low in the frame -- a line, not a blob -- so
that red AprilTag signs, rear LEDs, and floor reflections don't false-fire. The
FSM decides whether a detection is "close enough" to count as an intersection
event (see LeadFSM); this module only reports the geometry.
"""
import os
from typing import Optional

import cv2
import numpy as np
import yaml

from tasks.project.packages.world_model import RedLineObs

_HSV_FILE = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "config", "lead_stopline_hsv_config.yaml"
))

# Red wraps hue=0 in OpenCV's H=[0,180] space; two ranges OR'd. Stop-line tape is
# typically less saturated / brighter than an LED, hence a separate config.
_FALLBACK_HSV = {
    "red_low_h_min":  0,  "red_low_h_max":  12,
    "red_high_h_min": 165, "red_high_h_max": 180,
    "s_min": 90, "s_max": 255,
    "v_min": 80, "v_max": 255,
}

_MORPH_KERNEL = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))


def _load_hsv(path: str) -> dict:
    try:
        with open(path) as f:
            return {**_FALLBACK_HSV, **(yaml.safe_load(f) or {})}
    except FileNotFoundError:
        print(f"[lead] stop-line HSV config not found at {path}; using fallback.")
        return dict(_FALLBACK_HSV)


class RedLineDetector:
    def __init__(self, cfg: Optional[dict] = None, hsv_path: Optional[str] = None):
        cfg = cfg or {}
        # Detection band as a fraction of frame height (low in the frame, where a
        # stop line actually appears as the bot approaches).
        self.band_top_frac = float(cfg.get("stopline_band_top_frac", 0.72))
        self.band_bot_frac = float(cfg.get("stopline_band_bot_frac", 0.98))
        # Geometric gates for "this is a line".
        self.min_width_frac = float(cfg.get("stopline_min_width_frac", 0.35))
        self.min_aspect     = float(cfg.get("stopline_min_aspect", 3.0))
        self.min_area_px    = float(cfg.get("stopline_min_area_px", 250))
        self._hsv = _load_hsv(hsv_path or _HSV_FILE)

    def reload_hsv(self, path: Optional[str] = None) -> None:
        self._hsv = _load_hsv(path or _HSV_FILE)

    def _red_mask(self, frame_bgr: np.ndarray) -> np.ndarray:
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        h = self._hsv
        low = cv2.inRange(hsv,
                          np.array([h["red_low_h_min"], h["s_min"], h["v_min"]]),
                          np.array([h["red_low_h_max"], h["s_max"], h["v_max"]]))
        high = cv2.inRange(hsv,
                           np.array([h["red_high_h_min"], h["s_min"], h["v_min"]]),
                           np.array([h["red_high_h_max"], h["s_max"], h["v_max"]]))
        mask = cv2.bitwise_or(low, high)
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, _MORPH_KERNEL)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, _MORPH_KERNEL)
        return mask

    def detect(self, frame_bgr: np.ndarray) -> RedLineObs:
        h, w = frame_bgr.shape[:2]
        y_top = max(0, int(self.band_top_frac * h))
        y_bot = min(h, int(self.band_bot_frac * h))
        if y_bot <= y_top:
            return RedLineObs(present=False, area_px=0.0, width_frac=0.0, dist_proxy=0.0)

        band = frame_bgr[y_top:y_bot, :]
        mask = self._red_mask(band)

        n, _, stats, centroids = cv2.connectedComponentsWithStats(mask, connectivity=8)
        best = None  # (area, width_frac, cy_global)
        for i in range(1, n):  # skip background
            x, y, bw, bh, area = stats[i]
            if area < self.min_area_px or bw == 0 or bh == 0:
                continue
            aspect = bw / float(bh)
            width_frac = bw / float(w)
            if aspect < self.min_aspect or width_frac < self.min_width_frac:
                continue  # blob, not a line
            cy_global = y_top + float(centroids[i][1])
            if best is None or area > best[0]:
                best = (float(area), float(width_frac), cy_global)

        if best is None:
            return RedLineObs(present=False, area_px=0.0, width_frac=0.0, dist_proxy=0.0)

        area, width_frac, cy_global = best
        # 0 at the top of the band (far), 1 at the bottom (near/closest).
        dist_proxy = (cy_global - y_top) / max(1.0, float(y_bot - y_top))
        return RedLineObs(present=True, area_px=area, width_frac=width_frac,
                          dist_proxy=float(max(0.0, min(1.0, dist_proxy))))
