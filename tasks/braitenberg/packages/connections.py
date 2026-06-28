from typing import Tuple
import numpy as np


def get_motor_left_matrix(shape: Tuple[int, int]) -> np.ndarray:
    """Left motor weight matrix: highest at bottom-left, decreasing toward top-right."""
    h, w = shape

    # Row weights: highest at bottom (row h-1 = 1.0), lowest at top (row 0 ≈ 0)
    row_weights = np.linspace(0, 1, h).reshape(h, 1)  # shape (h, 1)

    # Column weights: highest at left (col 0 = 1.0), lowest at right (col w-1 ≈ 0)
    col_weights = np.linspace(1, 0, w).reshape(1, w)  # shape (1, w)

    # Combine: a pixel at bottom-left gets weight ~1*1=1, top-right gets ~0*0=0
    W = row_weights * col_weights  # shape (h, w)

    return W


def get_motor_right_matrix(shape: Tuple[int, int]) -> np.ndarray:
    """Right motor weight matrix: highest at bottom-right, decreasing toward top-left."""
    h, w = shape

    # Row weights: highest at bottom (same as left)
    row_weights = np.linspace(0, 1, h).reshape(h, 1)  # shape (h, 1)

    # Column weights: highest at right (col w-1 = 1.0), lowest at left (col 0 ≈ 0)
    col_weights = np.linspace(0, 1, w).reshape(1, w)  # shape (1, w)

    # Combine: bottom-right gets ~1*1=1, top-left gets ~0*0=0
    W = row_weights * col_weights  # shape (h, w)

    return W
