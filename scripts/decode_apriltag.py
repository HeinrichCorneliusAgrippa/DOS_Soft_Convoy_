#!/usr/bin/env python3
"""Decode AprilTag IDs from a photo — no bot required.

Uses the same cv2.aruco detector the lead bot uses (tasks/project/packages/signs.py).
Tries the lead's configured family (tag36h11) first, then scans the other
AprilTag families so you still get an answer if the sign uses a different one.

Usage:
    python scripts/decode_apriltag.py path/to/photo.jpg [more.jpg ...]

Put the IDs it prints into config/project_lead_config.yaml -> apriltag_stop_ids.
"""
import sys

import cv2

# Same family -> OpenCV dictionary mapping as signs.py. tag36h11 is the
# Duckietown standard, so it's tried first.
_FAMILIES = [
    ("tag36h11", "DICT_APRILTAG_36h11"),
    ("tag25h9",  "DICT_APRILTAG_25h9"),
    ("tag16h5",  "DICT_APRILTAG_16h5"),
    ("tag36h10", "DICT_APRILTAG_36h10"),
]


def _make_detector(dict_id):
    """Build a detector for both modern (>=4.7) and legacy cv2.aruco APIs."""
    aruco = cv2.aruco
    if hasattr(aruco, "getPredefinedDictionary"):
        ad = aruco.getPredefinedDictionary(dict_id)
    else:
        ad = aruco.Dictionary_get(dict_id)
    if hasattr(aruco, "ArucoDetector"):  # OpenCV >= 4.7
        det = aruco.ArucoDetector(ad, aruco.DetectorParameters())
        return lambda gray: det.detectMarkers(gray)
    params = aruco.DetectorParameters_create()
    return lambda gray: aruco.detectMarkers(gray, ad, parameters=params)


def decode(path):
    img = cv2.imread(path)
    if img is None:
        print(f"  ERROR: could not read image {path!r}")
        return
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)

    found_any = False
    for fam, dict_name in _FAMILIES:
        if not hasattr(cv2.aruco, dict_name):
            continue
        corners, ids, _ = _make_detector(getattr(cv2.aruco, dict_name))(gray)
        if ids is None or len(ids) == 0:
            continue
        found_any = True
        for tid, pts in zip(ids.flatten(), corners):
            p = pts.reshape(-1, 2)
            w = float(p[:, 0].max() - p[:, 0].min())
            h = float(p[:, 1].max() - p[:, 1].min())
            print(f"  family={fam:9s} id={int(tid):<4d} size~{int(w)}x{int(h)}px")

    if not found_any:
        print("  no AprilTags found. Tips: get closer / crop tight to the tag, "
              "ensure it's in focus and well lit, and check it's actually an "
              "AprilTag (not a decorative sign).")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    for path in sys.argv[1:]:
        print(f"{path}:")
        decode(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
