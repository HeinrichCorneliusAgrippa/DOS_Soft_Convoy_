from typing import Tuple
import os
import yaml
import numpy as np

_GAINS_FILE = os.path.join(os.path.dirname(__file__), '..', '..', '..', 'config', 'modcon_config.yaml')
try:
    with open(_GAINS_FILE) as _f:
        _g = yaml.safe_load(_f) or {}
except FileNotFoundError:
    _g = {}

K_P = _g.get('k_P', 0.0)
K_I = _g.get('k_I', 0.0)
K_D = _g.get('k_D', 0.0)
MAX_OMEGA = _g.get('max_omega', 8.0)
MIN_OMEGA = -MAX_OMEGA


def PIDController(
    v_0: float,
    theta_ref: float,
    theta_hat: float,
    prev_e: float,
    prev_int: float,
    delta_t: float,
) -> Tuple[float, float, float, float]:
    """
    PID controller for Duckiebot heading control.

    Args:
        v_0:       constant linear velocity (m/s)
        theta_ref: reference heading (radians)
        theta_hat: current estimated heading from odometry (radians)
        prev_e:    tracking error at previous time step (radians)
        prev_int:  integral error accumulated so far (radians·s)
        delta_t:   time elapsed since last call (seconds)

    Returns:
        v:      linear velocity command (m/s)
        omega:  angular velocity command (rad/s), clamped to [-MAX_OMEGA, MAX_OMEGA]
        e:      current tracking error, to be passed as prev_e next call
        e_int:  updated integral error, to be passed as prev_int next call
    """

    # Step 1: Tracking error, wrapped to [-pi, pi] to avoid jumps at ±180°
    e = theta_ref - theta_hat
    e = np.arctan2(np.sin(e), np.cos(e))

    # Step 2: Integral term — rectangle rule approximation of ∫e dt
    e_int = prev_int + e * delta_t

    # Anti-windup: clamp integral so it doesn't grow unbounded when saturated
    e_int = np.clip(e_int, -2.0, 2.0)

    # Step 3: Derivative term — finite difference approximation of de/dt
    e_der = (e - prev_e) / delta_t if delta_t > 0 else 0.0

    # Step 4: PID control law
    omega = K_P * e + K_I * e_int + K_D * e_der

    # Step 5: Clamp omega to physical limits
    omega = np.clip(omega, MIN_OMEGA, MAX_OMEGA)

    # Linear velocity is held constant
    v = v_0

    return v, omega, e, e_int