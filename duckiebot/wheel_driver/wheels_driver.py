from math import fabs, floor
import os
import yaml
import logging

import duckiebot.hat_driver as hat_driver
from .wheels_driver_abs import WheelsDriverAbs, WheelPWMConfiguration
from duckiebot.encoder_driver import WheelEncoderPair

MotorDirection = hat_driver.MotorDirection
uint8 = int
float1 = float

logger = logging.getLogger(__name__)


class DaguWheelsDriver(WheelsDriverAbs):
    """Class handling communication with motors with calibration support."""

    def __init__(self, left_config: WheelPWMConfiguration, right_config: WheelPWMConfiguration,
                 calibration_file: str = None):
        super(DaguWheelsDriver, self).__init__(left_config, right_config)

        # Initialize HAT and motors
        DTHAT = hat_driver.HATv3
        self.hat = DTHAT()
        self.leftMotor = self.hat.get_motor(1, "left")
        self.rightMotor = self.hat.get_motor(2, "right")

        # Load calibration
        if calibration_file is None:
            # Default path relative to this file
            current_dir = os.path.dirname(__file__)
            calibration_file = os.path.join(current_dir, '../../config/modcon_config.yaml')

        self._load_calibration(calibration_file)
        self.calibration_file = calibration_file

        # print out some stats
        this = self.__class__.__name__
        logger.debug(f"[{this}] Motor #1: {self.leftMotor}")
        logger.debug(f"[{this}] Motor #2: {self.rightMotor}")
        logger.debug(f"[{this}] Calibration: gain={self.gain}, trim={self.trim}")
        logger.debug(f"[{this}] Physical: R={self.radius}m, baseline={self.baseline}m")

        # Wheel encoders (optional — skipped if GPIO unavailable)
        try:
            self.encoders = WheelEncoderPair()
            logger.debug(f"[{this}] Encoders: left=BOARD12  right=BOARD35")
        except Exception as e:
            self.encoders = None
            logger.error(f"[{this}] Encoders unavailable: {e}")

        # pwm (executed, in the range [0, 1])
        self._executed_left: float1 = 0.0
        self._executed_right: float1 = 0.0
        self.set_wheels_speed(0, 0)

    @property
    def left_pwm(self):
        return self._executed_left

    @property
    def right_pwm(self):
        return self._executed_right

    def set_wheels_speed(self, left: float, right: float):
        # Apply calibration (gain and trim)
        left_calibrated = left * (self.gain - self.trim)
        right_calibrated = right * (self.gain + self.trim)

        # Clamp to [-1, 1] range after calibration
        left_calibrated = max(-1.0, min(1.0, left_calibrated))
        right_calibrated = max(-1.0, min(1.0, right_calibrated))

        pwml: uint8 = self._pwm_value(left_calibrated, self.left_config)
        pwmr: uint8 = self._pwm_value(right_calibrated, self.right_config)

        leftMotorMode = MotorDirection.RELEASE
        rightMotorMode = MotorDirection.RELEASE

        if fabs(left) < self.left_config.deadzone:
            pwml = 0
        elif left > 0:
            leftMotorMode = MotorDirection.FORWARD
        elif left < 0:
            leftMotorMode = MotorDirection.BACKWARD

        if fabs(right) < self.right_config.deadzone:
            pwmr = 0
        elif right > 0:
            rightMotorMode = MotorDirection.FORWARD
        elif right < 0:
            rightMotorMode = MotorDirection.BACKWARD

        # Keep encoders in sync with motor direction
        if self.encoders is not None:
            self.encoders.set_directions(
                left_forward=(leftMotorMode  == MotorDirection.FORWARD),
                right_forward=(rightMotorMode == MotorDirection.FORWARD),
            )

        # executed pwm values are floats in [-1, 1] encoding both speed and direction
        self._executed_left = (pwml * leftMotorMode.value) / 255.
        self._executed_right = (pwmr * rightMotorMode.value) / 255.

        if not self.pretend:
            self.leftMotor.set(leftMotorMode, pwml)
            self.rightMotor.set(rightMotorMode, pwmr)

    
    def set_velocity(self, v: float, omega: float):
        """
        Set robot velocity using kinematics.
      
        v: Linear velocity in m/s (forward/backward)
        omega: Angular velocity in rad/s (turning)
        """
        
        v = max(-self.v_max, min(self.v_max, v))
        omega = max(-self.omega_max, min(self.omega_max, omega))

        v_left = (v - omega * self.baseline / 2.0) / self.radius
        v_right = (v + omega * self.baseline / 2.0) / self.radius

        wheel_max = self.v_max / self.radius  # max wheel angular velocity [rad/s]
        left_normalized = v_left / wheel_max
        right_normalized = v_right / wheel_max

        left_normalized = max(-1.0, min(1.0, left_normalized))
        right_normalized = max(-1.0, min(1.0, right_normalized))

        self.set_wheels_speed(left_normalized ,right_normalized)



    @staticmethod
    def _pwm_value(v: float, wheel_config: WheelPWMConfiguration) -> uint8:
        pwm: uint8 = 0
        if fabs(v) > wheel_config.deadzone:
            effective_max = wheel_config.pwm_min + int((wheel_config.pwm_max - wheel_config.pwm_min) * wheel_config.power_limit)
            pwm = int(floor(fabs(v) * (effective_max - wheel_config.pwm_min) + wheel_config.pwm_min))
            return min(pwm, effective_max)
        return pwm

    def _load_calibration(self, filepath: str):
        try:
            with open(filepath, 'r') as f:
                calib = yaml.safe_load(f)

            self.gain = calib.get('gain', 1.0)
            self.trim = calib.get('trim', 0.0)
            power_limit = float(calib.get('power_limit', 1.0))
            self.left_config.power_limit  = power_limit
            self.right_config.power_limit = power_limit

            self.baseline = calib.get('baseline', 0.1)
            self.radius = calib.get('radius', 0.0318)

            self.v_max = calib.get('v_max', 1.0)
            self.omega_max = calib.get('omega_max', 8.0)
            self.k = calib.get('k', 27.0)

            logger.debug(f"[DaguWheelsDriver] Loaded calibration from {filepath}")

        except FileNotFoundError:
            logger.warning(f"[DaguWheelsDriver] Warning: Calibration file not found: {filepath}")
            logger.warning(f"[DaguWheelsDriver] Using default values")
            # Set defaults
            self.gain = 1.0
            self.trim = 0.0
            self.baseline = 0.1
            self.radius = 0.0318
            self.v_max = 1.0
            self.omega_max = 8.0
            self.k = 27.0

    def __del__(self):
        if hasattr(self, 'leftMotor'):
            self.leftMotor.set(MotorDirection.RELEASE)
        if hasattr(self, 'rightMotor'):
            self.rightMotor.set(MotorDirection.RELEASE)
        if hasattr(self, 'encoders') and self.encoders is not None:
            self.encoders.shutdown()
        if hasattr(self, 'hat'):
            del self.hat
