"""Circle-grid back-marker tracker for the follower.

The lead bot carries a symmetric circle grid (default 7x3 black dots on white)
on its back. cv2.findCirclesGrid locates all dots; from them we derive:
  - lateral: centroid x of the dots (order-invariant -> immune to the symmetric
    grid's 180 deg point-ordering ambiguity),
  - distance proxy: mean nearest-neighbour dot spacing in pixels (grows as the
    lead nears), used in place of the LED pair_px,
  - heading: a BEST-EFFORT left/right perspective-asymmetry cue (no camera
    intrinsics in this repo, so it is advisory only, not safety-critical).

findCirclesGrid is all-or-nothing (it needs the full lattice) and is the
expensive path when the grid is ABSENT, so we (a) downscale, (b) restrict to a
padded ROI around the last hit, and (c) keep a short lost-grace window.
"""
import math
import os
from dataclasses import dataclass
from typing import Optional, Tuple

import cv2
import numpy as np
import yaml

_CONFIG_FILE = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "config", "marker_grid_config.yaml"
))


@dataclass
class GridObs:
    midpoint: Tuple[float, float]   # full-res pixel centroid of the dots
    span_px: float                  # mean nearest-neighbour dot spacing (distance proxy)
    bbox: Tuple[int, int, int, int]
    score: float
    heading: Optional[float] = None  # best-effort, signed; +ve ~ leader yawed right


def _load_cfg(path: str) -> dict:
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}


class MarkerGridTracker:
    def __init__(self, cfg: Optional[dict] = None, config_path: Optional[str] = None):
        cfg = cfg if cfg is not None else _load_cfg(config_path or _CONFIG_FILE)
        cols = int(cfg.get("grid_cols", 7))
        rows = int(cfg.get("grid_rows", 3))
        self.pattern_size = (cols, rows)
        self.n_points = cols * rows
        self.downscale = float(cfg.get("grid_downscale", 0.5))
        self.roi_pad_px = int(cfg.get("grid_roi_pad_px", 60))
        self.lost_grace = int(cfg.get("grid_lost_grace_frames", 6))
        self.use_clustering = bool(cfg.get("grid_use_clustering", False))
        # The ROI is small, so search it at full resolution: dots stay above
        # the blob detector's min area out to roughly twice the range of the
        # downscaled search (key for re-finding the leader after a corner).
        self.roi_downscale = float(cfg.get("grid_roi_downscale", 1.0))
        # When fully lost, additionally search a full-res horizontal band
        # where a *distant* leader appears (near the horizon). Costs about as
        # much as the downscaled full-frame search and extends re-acquisition
        # range to ~2x; the downscaled full-frame pass still covers a close
        # leader (whose dots are too big to need full res).
        self.far_search = bool(cfg.get("grid_far_search", True))
        self.far_top_frac = float(cfg.get("grid_far_band_top_frac", 0.10))
        self.far_bot_frac = float(cfg.get("grid_far_band_bot_frac", 0.45))

        flags = cv2.CALIB_CB_SYMMETRIC_GRID
        if self.use_clustering:
            flags |= cv2.CALIB_CB_CLUSTERING
        self._flags = flags
        self._blob = self._make_blob_detector(cfg)

        self._last: Optional[GridObs] = None
        self._missed = 0

    def _make_blob_detector(self, cfg: dict):
        try:
            params = cv2.SimpleBlobDetector_Params()
            params.filterByColor = True
            params.blobColor = 0  # black dots on white
            params.filterByArea = True
            params.minArea = float(cfg.get("grid_blob_min_area", 10))
            params.maxArea = float(cfg.get("grid_blob_max_area", 8000))
            params.filterByCircularity = True
            params.minCircularity = float(cfg.get("grid_blob_min_circularity", 0.6))
            params.filterByInertia = False
            params.filterByConvexity = False
            return cv2.SimpleBlobDetector_create(params)
        except Exception:
            return None  # findCirclesGrid will fall back to its internal detector

    # --- core detection on a (possibly cropped) gray region --------------------
    def _find_in(self, gray_region: np.ndarray, ox: int, oy: int,
                 scale: Optional[float] = None) -> Optional[np.ndarray]:
        """Run findCirclesGrid on a region; return Nx2 full-frame centres or None."""
        s = self.downscale if scale is None else scale
        small = cv2.resize(gray_region, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) if s != 1.0 else gray_region
        try:
            if self._blob is not None:
                found, centers = cv2.findCirclesGrid(small, self.pattern_size, flags=self._flags, blobDetector=self._blob)
            else:
                found, centers = cv2.findCirclesGrid(small, self.pattern_size, flags=self._flags)
        except cv2.error:
            return None
        if not found or centers is None:
            return None
        pts = centers.reshape(-1, 2).astype(np.float32)
        pts /= max(s, 1e-6)            # back to region full-res
        pts[:, 0] += ox               # back to frame coords
        pts[:, 1] += oy
        return pts

    @staticmethod
    def _mean_nn_spacing(pts: np.ndarray) -> float:
        # Mean nearest-neighbour distance: stable distance proxy under tilt.
        d = np.linalg.norm(pts[:, None, :] - pts[None, :, :], axis=2)
        np.fill_diagonal(d, np.inf)
        return float(np.mean(np.min(d, axis=1)))

    @staticmethod
    def _heading(pts: np.ndarray) -> Optional[float]:
        # Best-effort: compare vertical extent of the left third vs right third.
        # Under perspective the nearer side appears taller. Advisory only.
        xs = pts[:, 0]
        order = np.argsort(xs)
        k = max(1, len(order) // 3)
        left = pts[order[:k]]
        right = pts[order[-k:]]
        hl = float(left[:, 1].max() - left[:, 1].min())
        hr = float(right[:, 1].max() - right[:, 1].min())
        denom = hl + hr
        if denom < 1e-3:
            return None
        return (hl - hr) / denom

    def _obs_from_points(self, pts: np.ndarray) -> GridObs:
        cx, cy = float(pts[:, 0].mean()), float(pts[:, 1].mean())
        x1, y1 = float(pts[:, 0].min()), float(pts[:, 1].min())
        x2, y2 = float(pts[:, 0].max()), float(pts[:, 1].max())
        return GridObs(
            midpoint=(cx, cy),
            span_px=self._mean_nn_spacing(pts),
            bbox=(int(x1), int(y1), int(x2), int(y2)),
            score=1.0,
            heading=self._heading(pts),
        )

    # --- public update ---------------------------------------------------------
    def update(self, frame_bgr: np.ndarray) -> Optional[GridObs]:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        pts = None

        # 1) ROI around last hit (bounds the expensive absent-grid search).
        if self._last is not None:
            x1, y1, x2, y2 = self._last.bbox
            rx1 = max(0, x1 - self.roi_pad_px)
            ry1 = max(0, y1 - self.roi_pad_px)
            rx2 = min(w, x2 + self.roi_pad_px)
            ry2 = min(h, y2 + self.roi_pad_px)
            if rx2 - rx1 > 10 and ry2 - ry1 > 10:
                pts = self._find_in(gray[ry1:ry2, rx1:rx2], rx1, ry1,
                                    scale=self.roi_downscale)

        # 2) Full-frame fallback (downscaled: catches a close leader).
        if pts is None:
            pts = self._find_in(gray, 0, 0)

        # 3) Far-band fallback at full resolution: a distant leader's dots are
        # below the blob detector's min area in the downscaled pass.
        if pts is None and self.far_search and self._last is None:
            by1 = int(h * self.far_top_frac)
            by2 = int(h * self.far_bot_frac)
            if by2 - by1 > 10:
                pts = self._find_in(gray[by1:by2, :], 0, by1, scale=1.0)

        if pts is not None and len(pts) == self.n_points:
            obs = self._obs_from_points(pts)
            self._last = obs
            self._missed = 0
            return obs

        # 4) Grace: reuse last estimate briefly so single-frame misses don't drop the lock.
        if self._last is not None:
            self._missed += 1
            if self._missed <= self.lost_grace:
                return self._last
            self._last = None
            self._missed = 0
        return None
