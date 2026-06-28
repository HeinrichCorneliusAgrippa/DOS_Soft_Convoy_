import os
import yaml
import smbus2
from typing import List

from .led_driver_abs import LEDsDriverAbs


class PWMLEDsDriver(LEDsDriverAbs):
    CHANNEL_RED = 0
    CHANNEL_GREEN = 1
    CHANNEL_BLUE = 2

    def __init__(self, config_path=None, debug=False):
        super().__init__(debug)

        if config_path is None:
            config_path = os.path.join(
                os.path.dirname(__file__), 'config.yaml'
            )

        with open(config_path, 'r') as f:
            self.config = yaml.safe_load(f)

        self.i2c_bus = self.config['i2c']['bus']
        self.i2c_address = self.config['i2c']['address']
        self.pwm_period = self.config['pwm']['period']

        self.bus = smbus2.SMBus(self.i2c_bus)

        self.all_off()

    def set_channel_intensity(self, led: int, channel: int, intensity: float):
        intensity = max(0.0, min(1.0, intensity))

        # Convert to PWM value (12-bit shifted left by 4)
        pwm_value = int(intensity * 255) << 4

        pwm_channel = 3 * led + channel

        self.bus.write_word_data(
            self.i2c_address,
            0x06 + 4 * pwm_channel,
            pwm_value & 0xFFFF
        )
        self.bus.write_word_data(
            self.i2c_address,
            0x08 + 4 * pwm_channel,
            self.pwm_period & 0xFFFF
        )

    def set_rgb(self, led: int, color: List[float]):
        self.set_channel_intensity(led, self.CHANNEL_RED, color[0])
        self.set_channel_intensity(led, self.CHANNEL_GREEN, color[1])
        self.set_channel_intensity(led, self.CHANNEL_BLUE, color[2])

    def release(self):
        try:
            self.all_off()
        except:
            pass
        try:
            self.bus.close()
        except:
            pass

    def set_white(self, led: int, brightness: float = 1.0):
        self.set_rgb(led, [brightness, brightness, brightness])

    def set_all_front(self, color: List[float]):
        self.set_rgb(0, color)  # Front left
        self.set_rgb(2, color)  # Front right

    def set_all_back(self, color: List[float]):
        self.set_rgb(3, color)  # Back left
        self.set_rgb(4, color)  # Back right


# Alias for compatibility
LEDDriver = PWMLEDsDriver
