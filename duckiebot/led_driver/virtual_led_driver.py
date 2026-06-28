from typing import List
from .led_driver_abs import LEDsDriverAbs


class VirtualLEDsDriver(LEDsDriverAbs):
    def __init__(self, debug=True):
        super().__init__(debug)
        self.led_state = {
            0: [0.0, 0.0, 0.0],  # Front left
            2: [0.0, 0.0, 0.0],  # Front right
            3: [0.0, 0.0, 0.0],  # Back left
            4: [0.0, 0.0, 0.0],  # Back right
        }
        if self._debug:
            print("[VirtualLED] Initialized (simulation mode)")

    def set_channel_intensity(self, led: int, channel: int, intensity: float):
        intensity = max(0.0, min(1.0, intensity))

        if led in self.led_state:
            self.led_state[led][channel] = intensity

        if self._debug:
            channel_name = ['R', 'G', 'B'][channel]
            led_name = {0: 'FL', 2: 'FR', 3: 'BL', 4: 'BR'}[led]
            print(f"[VirtualLED] {led_name} {channel_name}={intensity:.2f}")

    def set_rgb(self, led: int, color: List[float]):
        if led in self.led_state:
            self.led_state[led] = [
                max(0.0, min(1.0, color[0])),
                max(0.0, min(1.0, color[1])),
                max(0.0, min(1.0, color[2]))
            ]

        if self._debug:
            led_name = {0: 'Front Left', 2: 'Front Right', 3: 'Back Left', 4: 'Back Right'}[led]
            r, g, b = self.led_state[led]
            print(f"[VirtualLED] {led_name:11} RGB=({r:.2f}, {g:.2f}, {b:.2f})")

    def release(self):
        if self._debug:
            print("[VirtualLED] Releasing resources")
        self.all_off()

    def get_state(self, led: int) -> List[float]:
        return self.led_state.get(led, [0.0, 0.0, 0.0])

    def get_all_states(self) -> dict:
        return self.led_state.copy()
