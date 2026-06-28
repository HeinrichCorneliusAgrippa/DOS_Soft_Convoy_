import smbus2
import time

# PCA9685 Registers
MODE1 = 0x00
MODE2 = 0x01
PRESCALE = 0xFE
LED0_ON_L = 0x06
LED0_ON_H = 0x07
LED0_OFF_L = 0x08
LED0_OFF_H = 0x09


class PWM:
    """PCA9685 PWM driver using smbus2 (Adafruit_PWM_Servo_Driver compatible)."""

    def __init__(self, address=0x60, busnum=1, debug=False):
        self.address = address
        self.bus = smbus2.SMBus(busnum)
        self.debug = debug

        self._reset()

    def _reset(self):
        self.bus.write_byte_data(self.address, MODE1, 0x00)
        time.sleep(0.005)  # 5ms delay for oscillator

    def setPWMFreq(self, freq):
        if self.debug:
            print(f"Setting PWM frequency to {freq} Hz")

        # PCA9685 runs at 25MHz internal clock
        prescaleval = 25000000.0    # 25MHz
        prescaleval /= 4096.0       # 12-bit resolution
        prescaleval /= float(freq)
        prescaleval -= 1.0

        prescale = int(prescaleval + 0.5)

        if self.debug:
            print(f"Prescale value: {prescale}")

        oldmode = self.bus.read_byte_data(self.address, MODE1)

        newmode = (oldmode & 0x7F) | 0x10  # Sleep
        self.bus.write_byte_data(self.address, MODE1, newmode)

        self.bus.write_byte_data(self.address, PRESCALE, prescale)

        self.bus.write_byte_data(self.address, MODE1, oldmode)
        time.sleep(0.005)

        # Enable auto-increment
        self.bus.write_byte_data(self.address, MODE1, oldmode | 0x80)

    def setPWM(self, channel, on, off):
        if self.debug:
            print(f"Channel {channel}: ON={on}, OFF={off}")

        # Calculate register addresses for this channel
        # Each channel uses 4 registers: ON_L, ON_H, OFF_L, OFF_H
        base_reg = LED0_ON_L + 4 * channel

        # Write ON time (low byte, high byte)
        self.bus.write_byte_data(self.address, base_reg, on & 0xFF)
        self.bus.write_byte_data(self.address, base_reg + 1, on >> 8)

        # Write OFF time (low byte, high byte)
        self.bus.write_byte_data(self.address, base_reg + 2, off & 0xFF)
        self.bus.write_byte_data(self.address, base_reg + 3, off >> 8)

    def setAllPWM(self, on, off):
        # Use special "all channels" registers (0xFA-0xFD)
        self.bus.write_byte_data(self.address, 0xFA, on & 0xFF)
        self.bus.write_byte_data(self.address, 0xFB, on >> 8)
        self.bus.write_byte_data(self.address, 0xFC, off & 0xFF)
        self.bus.write_byte_data(self.address, 0xFD, off >> 8)

    def __del__(self):
        try:
            self.bus.close()
        except:
            pass
