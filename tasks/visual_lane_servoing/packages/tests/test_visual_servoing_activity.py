"""Tests for lane marking segmentation.

Run:  python3 tasks/visual_lane_servoing/packages/tests/test_visual_servoing_activity.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")))

try:
    import cv2
    import numpy as np
except ImportError:
    print("SKIP: cv2/numpy not available")
    sys.exit(0)

from tasks.visual_lane_servoing.packages.visual_servoing_activity import detect_lane_markings  # noqa: E402


def _solid_bar_bgr(h, w, row0, row1, col0, col1, bgr):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    img[row0:row1, col0:col1] = bgr
    return img


def test_yellow_blob_fill_preserves_interior():
    h, w = 240, 320
    crop_top = int(h * 0.4)
    bgr = _solid_bar_bgr(h, w, crop_top + 30, crop_top + 55, 200, 280, (0, 255, 255))
    yellow, _ = detect_lane_markings(bgr)
    roi = yellow[crop_top:, :]
    assert int(np.count_nonzero(roi)) > 200


def test_white_blob_fill_preserves_interior():
    h, w = 240, 320
    crop_top = int(h * 0.4)
    bgr = _solid_bar_bgr(h, w, crop_top + 30, crop_top + 55, 20, 80, (255, 255, 255))
    _, white = detect_lane_markings(bgr)
    roi = white[crop_top:, :]
    assert int(np.count_nonzero(roi)) > 200


def test_gray_road_not_whole_left_panel():
    """Broad white HSV must not paint the entire left road gray as white."""
    h, w = 240, 320
    crop_top = int(h * 0.4)
    bgr = np.full((h, w, 3), (200, 190, 185), dtype=np.uint8)  # gray road
    bgr[crop_top + 30:crop_top + 55, 25: 70] = (255, 255, 255)  # left white tape
    _, white = detect_lane_markings(bgr)
    roi = white[crop_top:, :]
    filled = int(np.count_nonzero(roi))
    assert filled < roi.size * 0.25


def _run():
    test_yellow_blob_fill_preserves_interior()
    test_white_blob_fill_preserves_interior()
    test_gray_road_not_whole_left_panel()
    print("PASSED visual_servoing_activity tests")


if __name__ == "__main__":
    _run()
