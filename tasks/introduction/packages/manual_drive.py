from typing import Dict, Tuple
import logging
logger = logging.getLogger(__name__)

SPEED = 1
TURN = 0.5


def get_motor_speeds(keys_pressed: Dict[str, bool]) -> Tuple[float, float]:
    left = 0.0
    right = 0.0
    # Forward / backward
    if keys_pressed.get("up", False):
        left += SPEED
        right += SPEED
    if keys_pressed.get("down", False):
        left -= SPEED
        right -= SPEED
    # Turning
    if keys_pressed.get("left", False):
        left -= TURN
        right += TURN
    if keys_pressed.get("right", False):
        left += TURN
        right -= TURN
    # Clamp to valid range
    left = max(-1.0, min(1.0, left))
    right = max(-1.0, min(1.0, right))

    return left, right
