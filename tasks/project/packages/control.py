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


def apply_deadzone(left: float, right: float, deadzone: float) -> Tuple[float, float]:
    """Lift the wheels so the slower (inner) wheel clears the motor breakaway,
    WITHOUT flattening the steering differential, so the low-speed CURVE/LANE
    creep both moves AND keeps turning. When the inner wheel sits below the
    deadzone, both wheels are shifted up by the same deficit — preserving
    (right - left) — rather than clamping each wheel independently (which would
    pull the inner wheel up to the outer and kill the turn, so the bot runs
    straight off a curve). A commanded full stop (both <= 0) stays stopped.
    Real bot only: the caller gates on the absence of a sim pose, since the
    simulator's ideal motors have no deadzone. deadzone <= 0 is a no-op."""
    if deadzone <= 0.0:
        return left, right
    lo = min(left, right)
    hi = max(left, right)
    if hi <= 0.0:
        return left, right          # commanded stop: leave it stopped
    if lo < deadzone:               # inner wheel can't break away: lift both
        bump = deadzone - lo        # by the deficit so the differential survives
        left += bump
        right += bump
    return _clip01(left), _clip01(right)


def apply_leds(leds, d: Decision) -> None:
    if leds is None:
        return
    for idx, color in d.leds.items():
        try:
            leds.set_rgb(idx, list(color))
        except Exception:
            pass


def blink_all(leds, color, now, hz=2.0) -> None:
    """Blink all LEDs at `hz`. `color` is RGB in [0,1]; off on the dark phase."""
    if leds is None:
        return
    on = int(now * hz * 2) % 2 == 0
    rgb = list(color) if on else [0.0, 0.0, 0.0]
    for idx in (0, 2, 3, 4):
        try:
            leds.set_rgb(idx, rgb)
        except Exception:
            pass


def _clip01(x: float) -> float:
    if x < 0.0:
        return 0.0
    if x > 1.0:
        return 1.0
    return float(x)
