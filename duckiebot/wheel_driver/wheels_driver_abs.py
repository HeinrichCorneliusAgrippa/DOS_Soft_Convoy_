import dataclasses
from abc import ABC, abstractmethod

uint8 = int
float01 = float

@dataclasses.dataclass
class WheelPWMConfiguration:
    """
    Fields:
        pwm_min:     minimum speed as uint8 [0, 255] — overcomes motor stiction
        pwm_max:     maximum speed as uint8 [0, 255]
        deadzone:    ignore inputs below this fraction [0, 1)
        power_limit: cap effective max as fraction of pwm_max [0, 1]
                     e.g. 0.85 → motor never exceeds 85% of pwm_max
    """
    pwm_min: uint8 = 60
    pwm_max: uint8 = 255
    deadzone: float01 = 0.01
    power_limit: float01 = 1.0

class WheelsDriverAbs(ABC):

    def __init__(self, left_config: WheelPWMConfiguration, right_config: WheelPWMConfiguration):
        self.left_config = left_config
        self.right_config = right_config
        self.pretend: bool = False

    @abstractmethod
    def set_wheels_speed(self, left: float, right: float):
        """Set wheel speeds, each in [-1, 1]."""
        pass

    @property
    @abstractmethod
    def left_pwm(self) -> float:
        pass

    @property
    @abstractmethod
    def right_pwm(self) -> float:
        pass
