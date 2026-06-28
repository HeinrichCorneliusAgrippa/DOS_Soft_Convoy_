try:
    from .encoder_driver import WheelEncoder, WheelEncoderPair, LEFT_PIN, RIGHT_PIN, TICKS_PER_REV
except ImportError:
    WheelEncoder     = None
    WheelEncoderPair = None

__all__ = ['WheelEncoder', 'WheelEncoderPair', 'LEFT_PIN', 'RIGHT_PIN', 'TICKS_PER_REV']
