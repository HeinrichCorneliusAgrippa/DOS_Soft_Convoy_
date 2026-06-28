# Base classes (always available)
from .wheels_driver_abs import WheelsDriverAbs, WheelPWMConfiguration

# Simulation driver (for Godot)
from .godot_wheels_driver import GodotWheelsDriver

# Hardware driver (only available on Jetson)
try:
    from .wheels_driver import DaguWheelsDriver
except ImportError:
    DaguWheelsDriver = None  # Not available on non-Jetson systems

__all__ = [
    'WheelsDriverAbs',
    'WheelPWMConfiguration',
    'GodotWheelsDriver',
    'DaguWheelsDriver',
]
