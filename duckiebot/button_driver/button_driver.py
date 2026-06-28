import time
import subprocess
import sys
from enum import IntEnum

# Add file logging to ensure shutdown logs are saved
LOG_FILE = '/tmp/button-shutdown.log'

def log_print(msg):
    """Print to stdout and log file."""
    """Print to stdout and log file."""
    print(msg)
    sys.stdout.flush()
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(msg + '\n')
            f.flush()
    except:
        pass

import Jetson.GPIO as GPIO


class ButtonEvent(IntEnum):
    PRESS = 0
    RELEASE = 1


class ButtonLED:
    """Controls the button's LED on GPIO 37"""

    def __init__(self, gpio_pin=37):
        self.gpio_pin = gpio_pin
        self._is_shutdown = False

        GPIO.setmode(GPIO.BOARD)
        GPIO.setup(self.gpio_pin, GPIO.OUT)
        self.on()

    def on(self):
        if not self._is_shutdown:
            GPIO.output(self.gpio_pin, GPIO.HIGH)

    def off(self):
        if not self._is_shutdown:
            GPIO.output(self.gpio_pin, GPIO.LOW)

    def blink(self, duration_sec=3, freq_hz=2):
        transition_duration = 1.0 / (2 * freq_hz)
        num_cycles = int(duration_sec * freq_hz)

        for _ in range(num_cycles):
            self.off()
            time.sleep(transition_duration)
            self.on()
            time.sleep(transition_duration)

    def shutdown(self):
        self._is_shutdown = True
        self.off()


class ButtonDriver:

    HOLD_TIME_SHUTDOWN = 3 

    def __init__(self, led_gpio_pin=37, signal_gpio_pin=40, callback=None):
        if not 1 <= signal_gpio_pin <= 40:
            raise ValueError("Signal pin must be in range [1, 40]")

        self.signal_gpio_pin = signal_gpio_pin
        self.callback = callback
        self._press_start_time = None

        GPIO.setmode(GPIO.BOARD)
        GPIO.setup(self.signal_gpio_pin, GPIO.IN)
        self.led = ButtonLED(led_gpio_pin)
        GPIO.add_event_detect(
            self.signal_gpio_pin,
            GPIO.BOTH,
            callback=self._button_callback
        )

        print("[Button Driver] Started - Hold button 3 sec to shutdown")

    def _button_callback(self, pin):
        signal = int(GPIO.input(pin))
        event = ButtonEvent(signal)

        if event == ButtonEvent.PRESS:
            self._press_start_time = time.time()
            if self.callback:
                self.callback(event)

        elif event == ButtonEvent.RELEASE:
            if self._press_start_time is None:
                return  # Missed the press event

            duration = time.time() - self._press_start_time
            self._press_start_time = None

            # Check if held for shutdown duration
            if duration >= self.HOLD_TIME_SHUTDOWN:
                log_print(f"[Button] Held for {duration:.1f}s - Initiating shutdown")
                self._initiate_shutdown()
            else:
                log_print(f"[Button] Quick press ({duration:.1f}s) - No action")

            if self.callback:
                self.callback(event)

    def _initiate_shutdown(self):
        """Blink LED, shutdown battery/HAT/LEDs, then OS shutdown."""
        """Blink LED, shutdown battery/HAT/LEDs, then OS shutdown."""
        log_print("[Shutdown] Starting shutdown sequence...")

        self.led.blink(duration_sec=2, freq_hz=3)

        try:
            self._shutdown_battery()
        except Exception as e:
            log_print(f"[Shutdown] Battery shutdown failed: {e}")

        try:
            self._shutdown_hat()
        except Exception as e:
            log_print(f"[Shutdown] HAT sleep failed: {e}")

        try:
            self._shutdown_leds()
        except Exception as e:
            log_print(f"[Shutdown] LED shutdown failed: {e}")

        self.led.blink(duration_sec=1, freq_hz=5)
        self.led.off()

        log_print("[Shutdown] Shutting down OS...")
        subprocess.run(['sudo', 'shutdown', '-h', 'now'])

    def _shutdown_hat(self):
        import smbus2

        HAT_ADDRESS = 0x60
        MODE1_REG = 0x00
        SLEEP_BIT = 0x10

        bus = smbus2.SMBus(1)

        # Stop all PWM outputs first
        for reg in [0xFA, 0xFB, 0xFC, 0xFD]:  # All PWM channels off
            bus.write_byte_data(HAT_ADDRESS, reg, 0x00)

        # Put chip to sleep
        current_mode = bus.read_byte_data(HAT_ADDRESS, MODE1_REG)
        sleep_mode = (current_mode & 0x7F) | SLEEP_BIT
        bus.write_byte_data(HAT_ADDRESS, MODE1_REG, sleep_mode)

        bus.close()
        log_print("[HAT] Sleep mode activated")

    def _shutdown_leds(self):
        import smbus2

        LED_ADDRESS = 0x40

        try:
            bus = smbus2.SMBus(1)

            # Turn off all LED PWM channels
            for reg in [0xFA, 0xFB, 0xFC, 0xFD]:
                bus.write_byte_data(LED_ADDRESS, reg, 0x00)

            bus.close()
            log_print("[LEDs] All LEDs turned off")
        except:
            pass  # LED driver might not be present

    def _shutdown_battery(self):
        """Send shutdown command to DuckieBattery (firmware v2.0.1+)"""
        import serial
        import time

        log_print("[Battery] Opening serial port /dev/ttyACM0...")
        try:
            ser = serial.Serial('/dev/ttyACM0', 9600, timeout=2)
            log_print("[Battery] Serial port opened successfully")
            time.sleep(0.5)

            # Flush any pending telemetry data
            log_print("[Battery] Flushing serial buffers...")
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            time.sleep(0.3)

            # Send shutdown command multiple times to ensure delivery
            log_print("[Battery] Sending QQ command (attempt 1)...")
            ser.write(b'QQ')
            ser.flush()
            time.sleep(1.0)

            # Check for acknowledgment
            if ser.in_waiting > 0:
                response = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                log_print(f"[Battery] Response: {response[:100]}")
                if 'QACK' in response:
                    log_print("[Battery] ✓ Shutdown acknowledged (QACK)")
                    ser.close()
                    return
                elif 'TTL' in response:
                    log_print("[Battery] ✓ Shutdown countdown started (TTL)")
                    ser.close()
                    return

            # Try second attempt if first failed
            log_print("[Battery] No QACK received, trying again...")
            ser.reset_input_buffer()
            ser.reset_output_buffer()
            time.sleep(0.2)

            log_print("[Battery] Sending QQ command (attempt 2)...")
            ser.write(b'QQ')
            ser.flush()
            time.sleep(1.5)

            if ser.in_waiting > 0:
                response = ser.read(ser.in_waiting).decode('utf-8', errors='ignore')
                log_print(f"[Battery] Response: {response[:100]}")
                if 'QACK' in response or 'TTL' in response:
                    log_print("[Battery] ✓ Shutdown command accepted")

            ser.close()
            log_print("[Battery] Serial port closed - Battery will power off in ~60 seconds")

        except Exception as e:
            log_print(f"[Battery] ERROR: {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()

    def shutdown(self):
        GPIO.remove_event_detect(self.signal_gpio_pin)
        if hasattr(self, 'led'):
            self.led.shutdown()
        print("[Button Driver] Stopped")


def main():
    try:
        # Force cleanup any existing GPIO state
        try:
            GPIO.cleanup()
        except:
            pass

        driver = ButtonDriver()

        print("[Button Driver] Running... Press Ctrl+C to stop")
        print("[Button Driver] Hold top button for 3 seconds to shutdown")

        # Keep running
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[Button Driver] Interrupted by user")

    finally:
        if 'driver' in locals():
            driver.shutdown()
        GPIO.cleanup()


if __name__ == '__main__':
    main()
