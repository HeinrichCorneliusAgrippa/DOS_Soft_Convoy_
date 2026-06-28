from typing import Tuple

# Path to the trained model weights (.onnx file).
# Relative paths resolve from the project root.
MODEL_PATH = "tasks/object_detection/models/best.onnx"


def NUMBER_FRAMES_SKIPPED() -> int:
    # Higher = run inference less often (cheaper).
    return 2


def filter_by_classes(pred_class: int) -> bool:
    """Return False to drop this prediction."""
    return True


def filter_by_scores(score: float) -> bool:
    """Confidence in [0.0, 1.0]. Return False to drop low-confidence boxes."""
    return score >= 0.6


def filter_by_bboxes(bbox: Tuple[int, int, int, int]) -> bool:
    """bbox is (xmin, ymin, xmax, ymax) in pixels. Return False to drop."""
    xmin, ymin, xmax, ymax = bbox
    width = xmax - xmin
    height = ymax - ymin
    area = width * height
    return area > 800
