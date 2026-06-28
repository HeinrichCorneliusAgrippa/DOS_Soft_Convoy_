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
        self._apriltag = None         # dedicated AprilTag-lib detector (preferred)
        self._apriltag_lib = None     # which lib provided it (for diagnostics)
        self._detector = None         # cv2.aruco fallback tuple, or None
        self._backend = self._make_backend(self.family)
        self.last_tag_ids: List[int] = []
        # Diagnostics: how many quad candidates the detector found but could NOT
        # decode into a valid ID last frame. >0 with no IDs => the tag's square is
        # being seen but the bits won't read (resolution/blur); 0 => not even a
        # quad (border missing / too close / occluded / too low contrast).
        self.last_rejected: int = 0
        # One-time build diagnostics (cv2 version, detector API path, and a
        # self-test: can this aruco build even detect its OWN generated marker?).
        # Surfaced via /status to tell a broken/old aruco apart from a bad image.
        self.diag = self._diagnostics()
        print(f"[lead][signs] {self.diag}")

    # --- backend selection -----------------------------------------------------
    def _make_backend(self, family: str) -> Optional[str]:
        """Prefer a dedicated AprilTag library (dt-apriltags / pupil-apriltags):
        it bundles the real AprilTag detector and needs no opencv-contrib — which
        matters on the Jetson, whose OpenCV 4.1.1 ships NO cv2.aruco. Fall back to
        cv2.aruco where it exists (dev machines / sim)."""
        Detector = None
        for modname in ("dt_apriltags", "pupil_apriltags"):
            try:
                mod = __import__(modname, fromlist=["Detector"])
                Detector = getattr(mod, "Detector")
                self._apriltag_lib = modname
                break
            except Exception:
                continue
        if Detector is not None:
            # Try richest kwargs first, then progressively simpler — older Jetson
            # builds of the lib may not accept every keyword.
            for kwargs in (
                dict(families=str(family), nthreads=1, quad_decimate=1.0,
                     quad_sigma=0.0, refine_edges=1, decode_sharpening=0.25),
                dict(families=str(family), nthreads=1, quad_decimate=1.0),
                dict(families=str(family)),
            ):
                try:
                    self._apriltag = Detector(**kwargs)
                    print(f"[lead] AprilTag backend: {self._apriltag_lib} (family={family})")
                    return "apriltag"
                except TypeError:
                    continue   # unknown kwarg on this lib version — try simpler
                except Exception as e:
                    print(f"[lead] {self._apriltag_lib} init failed ({e!r}).")
                    break
            self._apriltag = None
        self._detector = self._make_detector(family)
        if self._detector is not None:
            return "aruco"
        print("[lead] no AprilTag backend available (no dt-apriltags/"
              "pupil-apriltags, and no cv2.aruco); sign detection DISABLED.")
        return None

    # --- detector construction -------------------------------------------------
    def _make_detector(self, family: str):
        # Some OpenCV builds ship cv2 as a single extension module rather than a
        # package, so `import cv2.aruco` raises "'cv2' is not a package" even when
        # the aruco module is bundled and reachable as the attribute cv2.aruco.
        # Prefer the attribute; fall back to the submodule import (package builds).
        aruco = getattr(cv2, "aruco", None)
        if aruco is None:
            try:
                import cv2.aruco as aruco
            except Exception as e:  # pragma: no cover - depends on the cv2 build
                print(f"[lead] cv2.aruco unavailable ({e}); AprilTag sign "
                      f"detection DISABLED. Install opencv-contrib-python.")
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
        self.last_rejected = 0
        if self._backend == "apriltag":
            try:
                dets = self._apriltag.detect(gray)
            except Exception:
                return [], []
            corners = [np.asarray(d.corners, dtype=np.float32) for d in dets]
            return corners, [int(d.tag_id) for d in dets]
        if self._detector is None:
            return [], []
        mode, det = self._detector
        try:
            if mode == "obj":
                corners, ids, rejected = det.detectMarkers(gray)
            else:
                ad, params = det
                corners, ids, rejected = self._aruco.detectMarkers(gray, ad, parameters=params)
        except cv2.error:
            return [], []
        self.last_rejected = 0 if rejected is None else len(rejected)
        if ids is None:
            return [], []
        return corners, [int(i) for i in ids.flatten()]

    def _diagnostics(self) -> dict:
        """Report the active backend + a self-test through it: render a known
        tag36h11 marker and try to detect it. selftest ids==[25] => the backend
        works (any real-image miss is then the image); ids==[] / disabled =>
        the backend is the problem."""
        d = {
            "backend": self._backend,
            "apriltag_lib": self._apriltag_lib,
            "cv2": cv2.__version__,
            "has_aruco": self._aruco is not None,
            "aruco_mode": (self._detector[0] if self._detector else None),
        }
        if self._backend is None:
            d["selftest"] = "no-backend"
            return d
        try:
            marker = self._render_test_marker(25)
            if marker is None:
                d["selftest"] = "skipped(no-renderer)"
            else:
                _, ids = self._detect_tags(marker)
                d["selftest"] = {"ids": ids}
        except Exception as e:  # pragma: no cover - depends on the build
            d["selftest"] = f"exc:{e!r}"
        return d

    def _render_test_marker(self, tid: int):
        """Render a tag36h11 marker for the self-test. Needs cv2.aruco to draw;
        returns None on builds without it (e.g. the Jetson) — there the real
        held-tag test is the check."""
        aruco = getattr(cv2, "aruco", None)
        if aruco is None or not hasattr(aruco, "DICT_APRILTAG_36h11"):
            return None
        ad = aruco.getPredefinedDictionary(aruco.DICT_APRILTAG_36h11)
        side = 240
        if hasattr(aruco, "generateImageMarker"):
            m = aruco.generateImageMarker(ad, tid, side)
        elif hasattr(aruco, "drawMarker"):
            m = aruco.drawMarker(ad, tid, side)
        else:
            return None
        canvas = np.full((side + 100, side + 100), 255, np.uint8)
        canvas[50:50 + side, 50:50 + side] = m
        return canvas

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
