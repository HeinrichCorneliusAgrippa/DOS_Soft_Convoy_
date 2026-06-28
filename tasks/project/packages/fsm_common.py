"""Shared, dependency-free FSM building blocks for the convoy roles.

Both the lead (`project_lead`) and follower (`project_follow`) FSMs build their
`Decision` from these helpers. Kept free of numpy/cv2 so the pure-logic FSMs can
be unit-tested without the vision stack installed.
"""
from dataclasses import dataclass, field
from typing import Dict, Tuple

LedColor = Tuple[float, float, float]

RED: LedColor    = (1.0, 0.0, 0.0)
YELLOW: LedColor = (1.0, 1.0, 0.0)
GREEN: LedColor  = (0.0, 1.0, 0.0)
BLUE: LedColor   = (0.0, 0.0, 1.0)
WHITE: LedColor  = (1.0, 1.0, 1.0)
OFF: LedColor    = (0.0, 0.0, 0.0)


@dataclass
class Decision:
    state_name: str
    base_speed: float
    steering: float
    leds: Dict[int, LedColor] = field(default_factory=dict)


def clamp(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return float(x)


def all_leds(color: LedColor) -> Dict[int, LedColor]:
    return {0: color, 1: color, 2: color, 3: color, 4: color}


def follow_speed(distance_proxy: float, safe: float, stop: float, cruise: float) -> float:
    """Linear taper on a *closeness* proxy that GROWS as the leader nears
    (inter-LED pixels, circle-grid span, or bbox height).

    proxy <= safe  -> full `cruise`;  proxy >= stop -> 0; linear between.
    """
    if stop <= safe:
        return cruise
    close = (distance_proxy - safe) / (stop - safe)
    close = clamp(close, 0.0, 1.0)
    return cruise * (1.0 - close)


def lateral_to_steer(lateral: float, p_gain: float, max_steer: float) -> float:
    """`lateral` is +ve when the leader is on the RIGHT. The wheel mix
    (left=base-steer, right=base+steer) needs +ve steer to turn RIGHT, so negate
    to steer TOWARD the leader (matches the lane-servoing sign convention)."""
    return clamp(-p_gain * lateral, -max_steer, max_steer)
