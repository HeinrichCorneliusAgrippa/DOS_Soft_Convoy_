import colorsys
from typing import List


def set_turning_leds(direction: str) -> dict:
    """Set LEDs to indicate turning direction."""
    yellow = [1.0, 1.0, 0.0]
    white = [1.0, 1.0, 1.0]
    red = [1.0, 0.0, 0.0]
    off = [0.0, 0.0, 0.0]
    direction = direction.lower()
    if direction == "left":
        return {
            0: yellow,  # front-left on
            2: off,  # front-right off
            3: off,  # back-right off
            4: yellow,  # back-left on
        }
    elif direction == "right":
        # Mirror of left: right-side LEDs on
        return {
            0: off,  # front-left
            2: yellow,  # front-right
            3: yellow,  # back-right
            4: off,  # back-left
        }
    elif direction == "forward":
        return {
            0: white,  # front-left
            2: white,  # front-right
            3: off,  # back-right
            4: off,  # back-left
        }
    elif direction == "stop":
        return {
            0: off,  # front-left
            2: off,  # front-right
            3: red,  # back-right
            4: red,  # back-left
        }
    else:
        # Unknown direction: all off
        return {
            0: off,
            2: off,
            3: off,
            4: off,
        }
