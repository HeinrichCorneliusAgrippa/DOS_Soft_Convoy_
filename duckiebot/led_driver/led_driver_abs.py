from abc import ABC, abstractmethod
from typing import List

uint8 = int


class LEDsDriverAbs(ABC):

    def __init__(self, debug=False):
        self._debug = debug

    @abstractmethod
    def set_channel_intensity(self, led: int, channel: int, intensity: float):
        pass

    @abstractmethod
    def set_rgb(self, led: int, color: List[float]):
        pass

    @abstractmethod
    def release(self):
        pass

    def all_on(self):
        for led in [0, 2, 3, 4]:
            self.set_rgb(led, [1.0, 1.0, 1.0])

    def all_off(self):
        for led in [0, 2, 3, 4]:
            self.set_rgb(led, [0.0, 0.0, 0.0])

    def __del__(self):
        self.release()
