import math
import threading

import Jetson.GPIO as GPIO

# BOARD pin numbers (BCM 18 and BCM 19 on Jetson Nano J41 header)
LEFT_PIN      = 12
RIGHT_PIN     = 35

TICKS_PER_REV = 135    # Hall effect pulses per full wheel revolution
WHEEL_RADIUS  = 0.0318 # metres


class WheelEncoder:
    """
    Single-channel Hall effect encoder for one Duckiebot DB21J wheel.

    Direction cannot be detected from the signal alone (single channel, no
    quadrature). Call set_direction() whenever the motor command changes so
    that the tick counter goes up when moving forward and down in reverse.
    """

    def __init__(self, pin: int, ticks_per_rev: int = TICKS_PER_REV):
        self._pin          = pin
        self._ticks_per_rev = ticks_per_rev
        self._ticks        = 0
        self._direction    = 1   # +1 = forward, -1 = reverse
        self._lock         = threading.Lock()

        # BOARD mode is already set by the motor driver; calling it again with
        # the same value is safe (Jetson.GPIO treats it as a no-op).
        GPIO.setmode(GPIO.BOARD)
        GPIO.setup(pin, GPIO.IN)
        GPIO.add_event_detect(pin, GPIO.RISING, callback=self._isr)

    def _isr(self, _channel):
        with self._lock:
            self._ticks += self._direction

    @property
    def ticks(self) -> int:
        with self._lock:
            return self._ticks

    def reset(self):
        with self._lock:
            self._ticks = 0

    def set_direction(self, forward: bool):
        """Call this every time the motor direction changes."""
        self._direction = 1 if forward else -1

    def revolutions(self) -> float:
        return self.ticks / self._ticks_per_rev

    def distance_m(self, wheel_radius: float = WHEEL_RADIUS) -> float:
        return self.revolutions() * (2 * math.pi * wheel_radius)

    def shutdown(self):
        GPIO.remove_event_detect(self._pin)


class WheelEncoderPair:
    """Convenience wrapper that holds both encoders together."""

    def __init__(self,
                 left_pin:  int = LEFT_PIN,
                 right_pin: int = RIGHT_PIN,
                 ticks_per_rev: int = TICKS_PER_REV):
        self.left  = WheelEncoder(left_pin,  ticks_per_rev)
        self.right = WheelEncoder(right_pin, ticks_per_rev)

    def reset(self):
        self.left.reset()
        self.right.reset()

    def set_directions(self, left_forward: bool, right_forward: bool):
        self.left.set_direction(left_forward)
        self.right.set_direction(right_forward)

    def shutdown(self):
        self.left.shutdown()
        self.right.shutdown()
