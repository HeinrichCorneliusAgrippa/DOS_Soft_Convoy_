from typing import Tuple
import os
import numpy as np
import cv2
import yaml

_HSV_FILE = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'config', 'lane_servoing_hsv_config.yaml')
try:
    with open(_HSV_FILE) as _f:
        _h = yaml.safe_load(_f) or {}
except FileNotFoundError:
    _h = {}

_yellow_lower = np.array([_h.get('yellow_lower_h', 0),  _h.get('yellow_lower_s', 0),  _h.get('yellow_lower_v', 0)])
_yellow_upper = np.array([_h.get('yellow_upper_h', 0),  _h.get('yellow_upper_s', 0), _h.get('yellow_upper_v', 0)])

_white_lower = np.array([_h.get('white_lower_h', 0),   _h.get('white_lower_s', 0), _h.get('white_lower_v', 0)])
_white_upper = np.array([_h.get('white_upper_h', 0), _h.get('white_upper_s', 0), _h.get('white_upper_v', 0)])

_SIGMA = 4.5
_MAG_THRESHOLD = 40.0


def detect_lane_markings(image: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    # Crop the top half (horizon) — only look at the road
    h, w = image.shape[:2]
    crop_top = int(h * 0.4)
    roi = image[crop_top:, :]

    # Blur to reduce noise
    blurred = cv2.GaussianBlur(roi, (5, 5), 2)

    # Convert to HSV
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)

    # Color masks
    yellow_mask = cv2.inRange(hsv, _yellow_lower, _yellow_upper)
    white_mask  = cv2.inRange(hsv, _white_lower, _white_upper)

    # Put masks back into full-size images
    full_yellow = np.zeros((h, w), dtype=np.uint8)
    full_white  = np.zeros((h, w), dtype=np.uint8)
    full_yellow[crop_top:, :] = yellow_mask
    full_white[crop_top:, :]  = white_mask

    return (full_yellow // 255).astype(np.uint8), (full_white // 255).astype(np.uint8)


def set_hsv_bounds(yellow_lower, yellow_upper, white_lower, white_upper):
    global _yellow_lower, _yellow_upper, _white_lower, _white_upper
    _yellow_lower = np.array(yellow_lower)
    _yellow_upper = np.array(yellow_upper)
    _white_lower  = np.array(white_lower)
    _white_upper  = np.array(white_upper)


def get_hsv_bounds():
    return {
        'yellow_lower_h': int(_yellow_lower[0]), 'yellow_upper_h': int(_yellow_upper[0]),
        'yellow_lower_s': int(_yellow_lower[1]), 'yellow_upper_s': int(_yellow_upper[1]),
        'yellow_lower_v': int(_yellow_lower[2]), 'yellow_upper_v': int(_yellow_upper[2]),
        'white_lower_h':  int(_white_lower[0]),  'white_upper_h':  int(_white_upper[0]),
        'white_lower_s':  int(_white_lower[1]),  'white_upper_s':  int(_white_upper[1]),
        'white_lower_v':  int(_white_lower[2]),  'white_upper_v':  int(_white_upper[2]),
    }