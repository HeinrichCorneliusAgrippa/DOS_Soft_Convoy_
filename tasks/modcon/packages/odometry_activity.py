from typing import Tuple
import numpy as np


def delta_phi(ticks: int, prev_ticks: int, resolution: int) -> Tuple[float, float]:
    delta_ticks = ticks - prev_ticks
    alpha = 2 * np.pi / resolution  # radians per tick
    rotation = delta_ticks * alpha  # total rotation in radians
    return rotation, ticks


def pose_estimation(
    R: float,
    baseline: float,
    x_prev: float,
    y_prev: float,
    theta_prev: float,
    delta_phi_left: float,
    delta_phi_right: float,
) -> Tuple[float, float, float]:
    """
    Estimates the new pose using dead-reckoning odometry.

    Args:
        R:               wheel radius (meters)
        baseline:        wheel-to-wheel distance (meters)
        x_prev:          previous x position (meters)
        y_prev:          previous y position (meters)
        theta_prev:      previous heading (radians)
        delta_phi_left:  left wheel rotation since last step (radians)
        delta_phi_right: right wheel rotation since last step (radians)

    Returns:
        (x, y, theta) — updated pose estimate
    """
    # Distance travelled by each wheel
    d_left  = R * delta_phi_left
    d_right = R * delta_phi_right

    # Distance and rotation of robot center
    d_A         = (d_left + d_right) / 2.0
    Delta_Theta = (d_right - d_left) / baseline

    # Update pose
    x_new     = x_prev + d_A * np.cos(theta_prev)
    y_new     = y_prev + d_A * np.sin(theta_prev)
    theta_new = theta_prev + Delta_Theta

    # Keep theta in [-pi, pi] to avoid unbounded accumulation
    theta_new = np.arctan2(np.sin(theta_new), np.cos(theta_new))

    return x_new, y_new, theta_new