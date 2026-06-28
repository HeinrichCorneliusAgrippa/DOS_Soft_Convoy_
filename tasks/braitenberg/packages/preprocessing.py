import os
import cv2
import numpy as np
import yaml

_HSV_FILE = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'config', 'braitenberg_hsv_config.yaml')
try:
    with open(_HSV_FILE) as _f:
        _h = yaml.safe_load(_f) or {}
except FileNotFoundError:
    _h = {}

lower_hsv = np.array([_h.get('lower_h', 0),   _h.get('lower_s', 0),   _h.get('lower_v', 0)])
upper_hsv = np.array([_h.get('upper_h', 179),  _h.get('upper_s', 255), _h.get('upper_v', 255)])


def preprocess(image_rgb: np.ndarray) -> np.ndarray:
    """Returns a 2D binary mask of pixels within the HSV range."""
    hsv = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2HSV)
    mask = cv2.inRange(hsv, lower_hsv, upper_hsv)
    return mask
