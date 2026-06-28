import cv2
import numpy as np

from tasks.object_detection.packages.agent import CLASS_NAMES, CLASS_COLORS


def draw_detections(image_bgr: np.ndarray, detections: list) -> np.ndarray:
    out = image_bgr
    h, w = out.shape[:2]

    for bbox, score, cls_id in detections:
        x1, y1, x2, y2 = bbox
        x1 = max(0, min(w - 1, x1))
        x2 = max(0, min(w - 1, x2))
        y1 = max(0, min(h - 1, y1))
        y2 = max(0, min(h - 1, y2))

        color = CLASS_COLORS.get(cls_id, (255, 255, 255))
        name  = CLASS_NAMES.get(cls_id, str(cls_id))
        label = f"{name} {score:.2f}"

        cv2.rectangle(out, (x1, y1), (x2, y2), color, 2)

        (tw, th), baseline = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        ly = max(0, y1 - th - baseline - 4)
        cv2.rectangle(out, (x1, ly), (x1 + tw + 6, ly + th + baseline + 4), color, -1)
        cv2.putText(out, label, (x1 + 3, ly + th + 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)

    return out


def draw_status_overlay(image_bgr: np.ndarray, message: str) -> np.ndarray:
    out = image_bgr.copy()
    h, w = out.shape[:2]
    pad = 10
    (tw, th), baseline = cv2.getTextSize(message, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 1)
    cv2.rectangle(out, (pad, pad), (pad + tw + 12, pad + th + baseline + 8), (0, 0, 0), -1)
    cv2.putText(out, message, (pad + 6, pad + th + 4),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 200, 255), 1, cv2.LINE_AA)
    return out
