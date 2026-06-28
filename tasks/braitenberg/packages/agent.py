import numpy as np
import dataclasses
from typing import Tuple

from .preprocessing import preprocess
from .connections import get_motor_left_matrix, get_motor_right_matrix


@dataclasses.dataclass
class BraitenbergAgentConfig:

    # Base forward speed in m/s (fraction of v_max)
    const: float = 0.3

    # Turning strength [0-1]: gain=1.0 → full omega_max turn when fully activated
    gain: float = 0.7

    # Noise threshold: ignore detections with fewer pixels than this
    detection_threshold: float = 1000.0

    # Max angular velocity (rad/s) — must match config/modcon_config.yaml omega_max
    omega_max: float = 8.0

    # Wheel baseline (m) — must match config/modcon_config.yaml baseline.
    # Used to clamp omega so the inner wheel never goes backward when moving forward.
    baseline: float = 0.1


class BraitenbergAgent:

    def __init__(self, config: BraitenbergAgentConfig = None):
        self.config = config or BraitenbergAgentConfig()
        self.left_matrix = None
        self.right_matrix = None

    def set_image_shape(self, shape: Tuple[int, int]):
        self.left_matrix = get_motor_left_matrix(shape)
        self.right_matrix = get_motor_right_matrix(shape)

    def compute_commands(self, image: np.ndarray) -> Tuple[float, float]:
        """
        Returns (v, omega):
            v     — forward speed [0-1] fraction of v_max
            omega — angular velocity [rad/s], positive = turn left
        """
        if self.left_matrix is None:
            self.set_image_shape(image.shape[:2])

        detection = preprocess(image)
        total_detection = np.sum(detection) / 255.0

        if total_detection < self.config.detection_threshold:
            return self.config.const, 0.0

        left_weighted  = detection * self.left_matrix
        right_weighted = detection * self.right_matrix

        ls = float(np.sum(left_weighted)) / (255.0 * total_detection)
        rs = float(np.sum(right_weighted)) / (255.0 * total_detection)

        v     = self.config.const
        omega = self.config.gain * (rs - ls) * self.config.omega_max

        if v > 0:
            omega_safe = v * 2.0 / self.config.baseline
            omega = max(-omega_safe, min(omega_safe, omega))

        return v, omega

    def step(self, image: np.ndarray, wheels_driver) -> Tuple[float, float]:
        v, omega = self.compute_commands(image)
        wheels_driver.set_velocity(v, omega)
        return wheels_driver.left_pwm, wheels_driver.right_pwm

    def get_debug_info(self, image: np.ndarray) -> dict:
        if self.left_matrix is None:
            self.set_image_shape(image.shape[:2])

        detection = preprocess(image)
        total_detection = np.sum(detection) / 255.0
        left_weighted  = detection * self.left_matrix
        right_weighted = detection * self.right_matrix
        left_sum  = np.sum(left_weighted)
        right_sum = np.sum(right_weighted)
        v, omega = self.compute_commands(image)

        return {
            'preprocessed':   detection / 255.0,
            'left_weighted':  left_weighted,
            'right_weighted': right_weighted,
            'left_sum':       left_sum,
            'right_sum':      right_sum,
            'diff':           left_sum - right_sum,
            'v':              v,
            'omega':          omega,
            'total_detection': total_detection,
            'threshold':      self.config.detection_threshold,
            'noise_filtered': total_detection < self.config.detection_threshold,
        }
