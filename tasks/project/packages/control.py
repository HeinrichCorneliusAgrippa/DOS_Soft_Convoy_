from typing import Tuple

from tasks.project.packages.fsm_common import Decision


def motors_from_decision(d: Decision) -> Tuple[float, float]:
    # Clamp steering to the base speed so a turn arcs forward instead of
    # collapsing into a single-wheel pivot; base==0 still yields a full stop.
    base = d.base_speed
    steer = max(-base, min(base, d.steering))
    left = base - steer
    right = base + steer
    return _clip01(left), _clip01(right)


def apply_leds(leds, d: Decision) -> None:
    if leds is None:
        return
    for idx, color in d.leds.items():
        try:
            leds.set_rgb(idx, list(color))
        except Exception:
            pass


def _clip01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return float(x)
