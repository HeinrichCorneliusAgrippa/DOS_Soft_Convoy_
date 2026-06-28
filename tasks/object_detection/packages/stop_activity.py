from typing import List, Tuple

Detection = Tuple[Tuple[int, int, int, int], float, int]

class_names = {0: 'duckie', 1: 'truck', 2: 'sign'}


def should_stop(detections: List[Detection], img_size: int) -> Tuple[bool, str]:
    """
    Decides whether the bot should stop based on filtered detections.

    Args:
        detections: list of ((x1, y1, x2, y2), score, class_id) that passed filters
        img_size:   height/width of the camera frame in pixels

    Returns:
        (True, reason) to stop, (False, "") to keep moving
    """
    for (x1, y1, x2, y2), score, class_id in detections:
        # Only react to duckies (class 0) — trucks and signs don't require stopping
        if class_id != 0:
            continue

        area = (x2 - x1) * (y2 - y1)
        close_vertically = y2 > img_size * _PROXIMITY_THRESHOLD

        if close_vertically and area > _AREA_THRESHOLD:
            reason = (
                f"Duckie detected ahead — "
                f"y2={y2} ({y2/img_size:.0%} of frame), "
                f"area={area}px², "
                f"confidence={score:.2f}"
            )
            return True, reason

    return False, ""

