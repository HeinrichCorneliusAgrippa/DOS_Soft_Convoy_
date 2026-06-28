import os
import yaml
import numpy as np
import cv2
from collections import deque
from typing import Tuple

from tasks.visual_lane_servoing.packages import visual_servoing_activity as student
from tasks.visual_lane_servoing.packages.curve_behavior import detect_curve

_CONFIG_FILE = os.path.normpath(os.path.join(
    os.path.dirname(__file__), '..', '..', '..', 'config', 'lane_servoing_config.yaml'
))

_ROI_START   = 0.47
_NUM_SLICES  = 3
_SLICE_TOL   = 5
_CLUSTER_GAP = 10   # px column gap that splits two clusters in a strip
_MIN_CLUSTER = 3    # unique columns; anything narrower is a speck

# Lane half width per slice (far, mid, near), px at 640 wide. Perspective makes
# the near slice span far wider than the far one, so a single shared value
# mis-centers every single-line fallback; these are learned online per slice.
_SLICE_HALF_WIDTHS = (175.0, 200.0, 270.0)


def _cluster_means(cols: np.ndarray) -> list:
    """Mean column of each contiguous cluster in a strip, left to right.
    Clusters narrower than _MIN_CLUSTER unique columns are specks: dropped."""
    xs = np.unique(cols)
    if xs.size == 0:
        return []
    splits = np.where(np.diff(xs) > _CLUSTER_GAP)[0] + 1
    return [float(np.mean(c)) for c in np.split(xs, splits) if c.size >= _MIN_CLUSTER]


def detect_lines_in_slices(
    mask_yellow: np.ndarray,
    mask_white:  np.ndarray,
    h: int,
) -> Tuple[list, list]:
    """Per-slice line x positions; entries are None where a strip has no
    detection, so both lists stay aligned with slice_ys.

    The road has white lines on BOTH outer edges; a plain mean over all white
    pixels lands between them (left of our lane's edge) and steers the bot
    onto the left lane. Pick the lane-edge cluster on the correct side of the
    yellow centerline: when yellow is right-of-center the edge is the leftmost
    white cluster; when yellow is left-of-center it is the rightmost.
    """
    slice_height = int(h * 0.35 / _NUM_SLICES)
    start_y      = int(h * _ROI_START)
    w            = mask_white.shape[1]
    yellow_xs, white_xs = [], []

    for i in range(_NUM_SLICES):
        y = start_y + i * slice_height + slice_height // 2

        strip_y  = mask_yellow[y - _SLICE_TOL: y + _SLICE_TOL, :]
        clusters = _cluster_means(np.where(strip_y > 0)[1])
        yellow_x = int(np.mean(clusters)) if clusters else None  # dashes = same centerline
        yellow_xs.append(yellow_x)

        strip_w  = mask_white[y - _SLICE_TOL: y + _SLICE_TOL, :]
        clusters = _cluster_means(np.where(strip_w > 0)[1])
        white_x  = None
        if clusters:
            if yellow_x is not None and yellow_x >= int(w * 0.55):
                white_x = int(clusters[0])
                if white_x >= yellow_x:
                    white_x = None
            else:
                white_x = int(clusters[-1])
                if yellow_x is not None and white_x <= yellow_x:
                    white_x = None
        white_xs.append(white_x)

    return _near_anchored(yellow_xs), _near_anchored(white_xs)


def _near_anchored(xs: list) -> list:
    """Keep only the contiguous run of picks that reaches the nearest strip.
    A line the bot is actually following always crosses the near strips; picks
    that exist only in far strips are another road's markings seen across a
    corner — exactly the frames where they would command the wrong direction.
    Lists are ordered far -> near."""
    out = list(xs)
    for i in range(len(out) - 2, -1, -1):   # from second-nearest up to far
        if out[i + 1] is None:
            out[i] = None
    return out


class LaneServoingAgent:

    def __init__(self, config_path: str = None):
        path = config_path or _CONFIG_FILE
        try:
            with open(path) as f:
                cfg = yaml.safe_load(f) or {}
        except Exception:
            cfg = {}

        self.p_gain              = cfg.get('p_gain',              0.1)
        self.d_gain              = cfg.get('d_gain',              0.35)
        self.max_steer           = cfg.get('max_steer',           0.4)
        self.base_speed          = cfg.get('base_speed',          0.2)
        self.curve_speed         = cfg.get('curve_speed',         0.2)
        self.curve_threshold     = cfg.get('curve_threshold',     15)
        self.steering_threshold  = cfg.get('steering_threshold',  0.2)
        self.curve_boost         = cfg.get('curve_boost',         1.3)
        self.detection_threshold = cfg.get('detection_threshold', 500)

        self.frame_count        = 0
        self._prev_error        = 0.0
        self._filtered_error    = 0.0
        self._half_widths       = list(_SLICE_HALF_WIDTHS)
        self._left_history      = deque(maxlen=3)
        self._right_history     = deque(maxlen=3)
        self.last_debug_info    = self._empty_debug_info(480, 640)

    def reset(self) -> None:
        """Clear PID/smoothing state between maneuvers; keeps the learned lane width."""
        self._prev_error     = 0.0
        self._filtered_error = 0.0
        self._left_history.clear()
        self._right_history.clear()

    @staticmethod
    def _nearest_pick(xs):
        for i in range(len(xs) - 1, -1, -1):   # lists are far -> near
            if xs[i] is not None:
                return i, xs[i]
        return None, None

    def _calculate_error(self, yellow_xs, white_xs, left_det, right_det, w):
        pairs = [(i, yx, wx) for i, (yx, wx) in enumerate(zip(yellow_xs, white_xs))
                 if yx is not None and wx is not None]
        ys = [x for x in yellow_xs if x is not None]
        ws = [x for x in white_xs  if x is not None]

        if left_det and right_det and pairs:
            # Center per paired strip only: slice widths differ a lot with
            # perspective, so mixing strip subsets for the two means skews
            # the center. The half widths learn per slice for the same reason.
            for i, yx, wx in pairs:
                measured = (wx - yx) / 2.0
                init = _SLICE_HALF_WIDTHS[i]
                if 60.0 < measured < 340.0:
                    self._half_widths[i] = float(np.clip(
                        0.9 * self._half_widths[i] + 0.1 * measured,
                        0.6 * init, 1.4 * init))
            error = w / 2.0 - float(np.mean([(yx + wx) / 2.0 for _, yx, wx in pairs]))
        elif left_det and ys:
            # Single line: steer off the NEAREST strip's pick — a curving line
            # poisons a cross-strip mean long before it leaves the near strip.
            i, yx = self._nearest_pick(yellow_xs)
            error = w / 2.0 - (yx + self._half_widths[i])
        elif right_det and ws:
            i, wx = self._nearest_pick(white_xs)
            error = w / 2.0 - (wx - self._half_widths[i])
        else:
            return float(self._prev_error)  # already normalized — hold it as-is

        return float(np.clip(error / (w / 2.0), -1.0, 1.0))

    def _calculate_steering(self, error: float) -> float:
        error_diff       = error - self._prev_error
        self._prev_error = error
        steering = self.p_gain * error + self.d_gain * error_diff
        return float(np.clip(steering, -self.max_steer, self.max_steer))

    def _motor_commands(self, steering: float, recovery: bool, is_curve: bool, both_visible: bool):
        if recovery:
            return 0.0, 0.0

        speed = self.curve_speed if is_curve else self.base_speed
        
        if not both_visible:
            speed *= 0.8

        left  = speed - steering
        right = speed + steering

        if is_curve and abs(steering) > self.steering_threshold:
            if steering > 0:
                right *= self.curve_boost
            else:
                left  *= self.curve_boost

        return float(np.clip(left, 0.0, 1.0)), float(np.clip(right, 0.0, 1.0))

    def _smooth(self, left, right, both_visible):
        buf = 2 if both_visible else 1
        if self._left_history.maxlen != buf:
            self._left_history  = deque(maxlen=buf)
            self._right_history = deque(maxlen=buf)
        self._left_history.append(left)
        self._right_history.append(right)
        return (sum(self._left_history)  / len(self._left_history),
                sum(self._right_history) / len(self._right_history))

    def compute_commands(self, image: np.ndarray) -> Tuple[float, float]:
        self.frame_count += 1
        bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)

        try:
            mask_left, mask_right = student.detect_lane_markings(bgr)
        except Exception as e:
            print(f"[Agent] detect_lane_markings error: {e}")
            return 0.0, 0.0

        mask_y = (mask_left  * 255).astype(np.uint8)
        mask_w = (mask_right * 255).astype(np.uint8)

        yellow_pixels = int(np.count_nonzero(mask_y))
        white_pixels  = int(np.count_nonzero(mask_w))
        total_pixels  = yellow_pixels + white_pixels

        combined = np.clip(mask_left + mask_right, 0, 1)
        self.last_debug_info = {
            'roi':               image,
            'lane_mask':         (combined * 255).astype(np.uint8),
            'white_mask':        mask_w,
            'yellow_mask':       mask_y,
            'total_lane_pixels': total_pixels,
            'lateral_error':     float(np.clip(self._prev_error, -1.0, 1.0)),
            'lane_detected':     total_pixels >= self.detection_threshold,
            'frame_count':       self.frame_count,
        }

        h, w      = mask_y.shape
        left_det  = yellow_pixels > 0
        right_det = white_pixels  > 0
        recovery  = total_pixels  < self.detection_threshold

        yellow_xs, white_xs = detect_lines_in_slices(mask_y, mask_w, h)
        both_visible        = left_det and right_det and not recovery
        is_curve, curve_dir = detect_curve(yellow_xs, white_xs, self.curve_threshold)

        raw_error            = self._calculate_error(yellow_xs, white_xs, left_det, right_det, w)
        self._filtered_error = 0.7 * self._filtered_error + 0.3 * raw_error
        steering             = self._calculate_steering(self._filtered_error)
        left, right          = self._motor_commands(steering, recovery, is_curve, both_visible)
        left, right          = self._smooth(left, right, both_visible)

        slice_height = int(h * 0.35 / _NUM_SLICES)
        start_y      = int(h * _ROI_START)
        self.last_debug_info.update({
            'yellow_xs': yellow_xs,
            'white_xs':  white_xs,
            'slice_ys':  [start_y + i * slice_height + slice_height // 2 for i in range(_NUM_SLICES)],
            'is_curve':  is_curve,
            'curve_dir': curve_dir,
        })

        return left, right

    def step(self, image: np.ndarray, wheels_driver) -> Tuple[float, float]:
        left, right = self.compute_commands(image)
        wheels_driver.set_wheels_speed(left, right)
        return left, right

    def get_debug_info(self, image: np.ndarray) -> dict:
        return self.last_debug_info

    def _empty_debug_info(self, h, w):
        return {
            'roi':               np.zeros((h, w, 3), dtype=np.uint8),
            'lane_mask':         np.zeros((h, w),    dtype=np.uint8),
            'white_mask':        np.zeros((h, w),    dtype=np.uint8),
            'yellow_mask':       np.zeros((h, w),    dtype=np.uint8),
            'total_lane_pixels': 0,
            'lateral_error':     0.0,
            'lane_detected':     False,
            'frame_count':       0,
        }