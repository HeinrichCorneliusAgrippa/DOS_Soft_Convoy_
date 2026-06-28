import time
import os
from threading import Thread, Event

try:
    from luma.core.interface.serial import i2c
    from luma.oled.device import ssd1306
    from luma.core.render import canvas
    from PIL import ImageFont
except ImportError:
    print("ERROR: luma.oled library not installed")
    print("Install with: sudo pip3 install luma.oled")
    exit(1)


class BatteryMonitor:

    def __init__(self, port='/dev/ttyACM0', baudrate=9600):
        self.port = port
        self.baudrate = baudrate
        self.soc = None
        self.voltage = None
        self.current = None
        self.temp = None
        self._last_read_time = 0
        self._read_interval = 2.0  # Read every 2 seconds

    def read_status(self):
        # Battery sends null bytes in JSON — use regex instead of json.loads
        # Battery sends null bytes in JSON — use regex instead of json.loads
        now = time.time()
        if now - self._last_read_time < self._read_interval:
            return True

        self._last_read_time = now

        try:
            import serial
            import re

            ser = serial.Serial(self.port, self.baudrate, timeout=2)
            time.sleep(0.2)

            ser.reset_input_buffer()
            ser.reset_output_buffer()

            # Battery sends ~1 JSON per second
            # Battery sends ~1 JSON per second
            time.sleep(1.5)

            if ser.in_waiting > 0:
                raw_data = ser.read(ser.in_waiting)
                data = raw_data.replace(b'\x00', b'').decode('utf-8', errors='ignore')

                soc_match = re.search(r'"SOC\(%\)":\s*(\d+)', data)
                if soc_match:
                    self.soc = int(soc_match.group(1))

                voltage_match = re.search(r'"CellVoltage\(mV\)":\s*(\d+)', data)
                if voltage_match:
                    self.voltage = int(voltage_match.group(1))

                current_match = re.search(r'"Current\(mA\)":\s*(-?\d+)', data)
                if current_match:
                    self.current = int(current_match.group(1))

                temp_match = re.search(r'"CellTemp\(degK\)":\s*(\d+)', data)
                if temp_match:
                    self.temp = int(temp_match.group(1))

                ser.close()
                return self.soc is not None

            ser.close()
            return False

        except Exception as e:
            # Silently fail - display will show "--"
            return False


class DisplayDriver:

    def __init__(self, i2c_bus=1, i2c_address=0x3C, update_rate=0.5, debug=False):
        self.i2c_bus = i2c_bus
        self.i2c_address = i2c_address
        self.update_rate = update_rate
        self.debug = debug

        serial = i2c(port=self.i2c_bus, address=self.i2c_address)
        self.display = ssd1306(serial)
        self.battery = BatteryMonitor()
        self._stop_event = Event()
        self._thread = None

        print(f"[Display] Initialized SSD1306 on I2C bus {i2c_bus}, address 0x{i2c_address:02X}")

        if self.debug:
            print("[Display] Debug mode enabled")

    def get_ip_address(self):
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "No Network"

    def get_cpu_temp(self):
        try:
            with open('/sys/class/thermal/thermal_zone0/temp', 'r') as f:
                temp = int(f.read().strip()) / 1000.0
                return f"{temp:.1f}C"
        except:
            return "N/A"

    def get_cpu_usage(self):
        try:
            with open('/proc/stat', 'r') as f:
                line = f.readline()
                fields = [float(x) for x in line.split()[1:]]
                idle = fields[3]
                total = sum(fields)
                usage = 100 * (1 - idle / total)
                return f"{usage:.0f}%"
        except:
            return "N/A"

    def draw_battery_icon(self, draw, x, y, width, height, percentage):
        draw.rectangle((x, y, x + width, y + height), outline=1, fill=0)
        terminal_w = 2
        draw.rectangle((x + width, y + height//3, x + width + terminal_w, y + 2*height//3), outline=1, fill=1)

        if percentage is not None:
            fill_width = int((width - 2) * percentage / 100)
            if fill_width > 0:
                draw.rectangle((x + 1, y + 1, x + 1 + fill_width, y + height - 1), fill=1)

    def render_display(self):
        success = self.battery.read_status()

        if self.debug:
            if success and self.battery.soc is not None:
                print(f"[Display] Battery: {self.battery.soc}% {self.battery.voltage}mV {self.battery.current}mA")
            else:
                print("[Display] Battery read failed or no data")

        with canvas(self.display) as draw:
            if self.battery.soc is not None:
                self.draw_battery_icon(draw, 100, 0, 20, 10, self.battery.soc)
                draw.text((0, 0), f"BAT: {self.battery.soc}%", fill=1)
            else:
                draw.text((0, 0), "BAT: --", fill=1)

            draw.line((0, 12, 128, 12), fill=1)

            ip = self.get_ip_address()
            draw.text((0, 15), f"IP: {ip}", fill=1)

            if self.battery.voltage is not None:
                voltage_v = self.battery.voltage / 1000.0
                draw.text((0, 25), f"V: {voltage_v:.2f}V", fill=1)

            if self.battery.current is not None:
                current_a = self.battery.current / 1000.0
                if current_a < 0:
                    draw.text((0, 35), f"I: {abs(current_a):.2f}A", fill=1)
                else:
                    draw.text((0, 35), f"I: +{current_a:.2f}A CHG", fill=1)

            cpu_temp = self.get_cpu_temp()
            draw.text((0, 45), f"CPU: {cpu_temp}", fill=1)

            hostname = os.uname().nodename
            draw.text((0, 55), f"{hostname}", fill=1)

    def _update_loop(self):
        period = 1.0 / self.update_rate

        while not self._stop_event.is_set():
            try:
                self.render_display()
            except Exception as e:
                print(f"[Display] Error: {e}")

            time.sleep(period)

    def start(self):
        if self._thread is None or not self._thread.is_alive():
            self._stop_event.clear()
            self._thread = Thread(target=self._update_loop, daemon=True)
            self._thread.start()
            print("[Display] Update loop started")

    def stop(self):
        if self._thread and self._thread.is_alive():
            self._stop_event.set()
            self._thread.join(timeout=2)
            print("[Display] Update loop stopped")

    def clear(self):
        with canvas(self.display) as draw:
            draw.rectangle((0, 0, 128, 64), fill=0)


def main():
    import sys
    debug = '--debug' in sys.argv

    try:
        driver = DisplayDriver(update_rate=0.5, debug=debug)
        driver.start()

        print("[Display Driver] Running... Press Ctrl+C to stop")

        # Keep running
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[Display Driver] Interrupted by user")

    finally:
        if 'driver' in locals():
            driver.stop()
            driver.clear()


if __name__ == '__main__':
    main()
