from typing import List, Optional, Tuple
import numpy as np


def detect_curve(yellow_xs: List[Optional[int]], white_xs: List[Optional[int]],
                 curve_threshold: int = 15,
    ) -> Tuple[bool, int]:
    """Per-line bend across the three slices. A straight road line projects to
    a straight image line, and the slice rows are equally spaced, so
    x0 - 2*x1 + x2 ~= 0 on a straight for ANY bot pose (the near-far shift,
    by contrast, has a large perspective-convergence baseline even when the
    road is dead straight). A curve bends the projected line.

    Needs the line visible in all three slices; lists are slice-aligned with
    None placeholders. Sign is flipped so positive keeps meaning "road curves
    left" (the old near-far convention).
    """
    bends = []
    for xs in (yellow_xs, white_xs):
        if len(xs) == 3 and all(x is not None for x in xs):
            bends.append(xs[0] - 2 * xs[1] + xs[2])

    if not bends:
        return False, 0

    avg_bend = -float(np.mean(bends))  # left curve: far slice pulled left -> raw bend < 0

    if abs(avg_bend) > curve_threshold:
        return True, int(avg_bend)

    return False, 0
