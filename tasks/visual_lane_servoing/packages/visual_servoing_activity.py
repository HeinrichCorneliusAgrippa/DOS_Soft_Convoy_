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

# Which side of the frame the bot's white lane edge sits on, and the x-fraction
# split. detect_lane_markings drops white blobs on the WRONG side (road glare /
# the opposite lane's edge vs the real one). Historically hardcoded 'left'/0.55,
# but the side depends on which lane the bot drives: e.g. V1's camera shows white
# on the RIGHT, where the old 'left' filter discarded it entirely (empty mask
# however well the HSV matched). Per-bot via the merged HSV config; default
# 'left'/0.55 keeps the previous behaviour for the sim and unconfigured bots.
_WHITE_EDGE_SIDE = str(_h.get('white_edge_side', 'left')).lower()
_WHITE_EDGE_X_FRAC = float(_h.get('white_edge_x_frac', 0.55))

_SIGMA = 4.5
_YELLOW_MAG_THRESHOLD = 30.0   # dashes are small; softer than the white gate
_WHITE_MAG_THRESHOLD = 28.0

# Desaturated / dim dash centers on dark road (glare washes S; distance drops V).
_PALE_YELLOW_LOWER = np.array([8, 22, 40])
_PALE_YELLOW_UPPER = np.array([65, 255, 255])
# Shadowed / distant white tape can read purple-gray (H~155 S~48-81). OR-ed with
# the main low-S white mask, then edge-gated — never used alone (road matches).
_TINTED_WHITE_LOWER = np.array([130, 35, 175])
_TINTED_WHITE_UPPER = np.array([179, 92, 255])


def _drop_small_blobs(mask: np.ndarray, min_area: int = 30) -> np.ndarray:
    num, labels, stats, _ = cv2.connectedComponentsWithStats(mask)
    out = np.zeros_like(mask)
    for i in range(1, num):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            out[labels == i] = 255
    return out


def _keep_blobs_touching_edges(color_mask: np.ndarray, edge_mask: np.ndarray) -> np.ndarray:
    num, labels = cv2.connectedComponents(color_mask)
    if num <= 1:
        return color_mask
    hit = np.unique(labels[edge_mask & (labels > 0)])
    hit = hit[hit > 0]
    if hit.size == 0:
        return np.zeros_like(color_mask)
    return np.where(np.isin(labels, hit), 255, 0).astype(np.uint8)


def _keep_blobs_on_side(mask: np.ndarray, side: str, x_frac: float) -> np.ndarray:
    """Drop blobs on the wrong side of the frame (road glare vs lane marking)."""
    h, w = mask.shape
    num, labels, stats, centroids = cv2.connectedComponentsWithStats(mask)
    if num <= 1:
        return mask
    out = np.zeros_like(mask)
    for i in range(1, num):
        cx = centroids[i][0]
        if side == 'left' and cx < w * x_frac:
            out[labels == i] = 255
        elif side == 'right' and cx > w * x_frac:
            out[labels == i] = 255
    return out


def _edge_gated_with_fallback(color_mask: np.ndarray, edge_mask: np.ndarray,
                              min_keep: int = 15, frac: float = 0.08) -> np.ndarray:
    raw_n = int(np.count_nonzero(color_mask))
    gated = _keep_blobs_touching_edges(color_mask, edge_mask)
    if raw_n == 0:
        return gated
    if int(np.count_nonzero(gated)) >= max(min_keep, int(frac * raw_n)):
        return gated
    return color_mask


def detect_lane_markings(image: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    h, w = image.shape[:2]
    crop_top = int(h * 0.4)
    roi = image[crop_top:, :]

    blurred = cv2.GaussianBlur(roi, (0, 0), _SIGMA)
    hsv = cv2.cvtColor(blurred, cv2.COLOR_BGR2HSV)

    yellow_mask = cv2.inRange(hsv, _yellow_lower, _yellow_upper)
    yellow_pale = cv2.inRange(hsv, _PALE_YELLOW_LOWER, _PALE_YELLOW_UPPER)
    yellow_mask = cv2.bitwise_or(yellow_mask, yellow_pale)
    white_bright = cv2.inRange(hsv, _white_lower, _white_upper)
    white_tinted = cv2.inRange(hsv, _TINTED_WHITE_LOWER, _TINTED_WHITE_UPPER)
    white_mask = cv2.bitwise_or(white_bright, white_tinted)

    gray = cv2.cvtColor(blurred, cv2.COLOR_BGR2GRAY)
    sobelx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    sobely = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    grad_mag = np.sqrt(sobelx * sobelx + sobely * sobely)
    yellow_strong = grad_mag > _YELLOW_MAG_THRESHOLD
    white_strong = grad_mag > _WHITE_MAG_THRESHOLD

    raw_yellow = yellow_mask.copy()
    # Yellow: hue is distinctive — edge-gate only when it keeps enough of the
    # raw mask. No side filter (far dashes on a curve sit toward image center).
    yellow_mask = _edge_gated_with_fallback(
        yellow_mask, yellow_strong, min_keep=8, frac=0.04)
    yellow_mask = cv2.morphologyEx(
        yellow_mask, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    yellow_mask = _drop_small_blobs(yellow_mask, min_area=8)
    if np.count_nonzero(yellow_mask) == 0 and np.count_nonzero(raw_yellow) > 0:
        yellow_mask = _drop_small_blobs(
            cv2.morphologyEx(raw_yellow, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))),
            min_area=8)

    # White: ALWAYS edge-gate — raw HSV matches gray road if S is too wide.
    # Small vertical close only; a tall kernel merged the whole left road panel.
    white_mask = _keep_blobs_touching_edges(white_mask, white_strong)
    white_mask = cv2.morphologyEx(
        white_mask, cv2.MORPH_CLOSE,
        cv2.getStructuringElement(cv2.MORPH_RECT, (5, 15)))
    white_mask = _drop_small_blobs(white_mask, min_area=35)
    white_mask = _keep_blobs_on_side(white_mask, _WHITE_EDGE_SIDE, _WHITE_EDGE_X_FRAC)

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
