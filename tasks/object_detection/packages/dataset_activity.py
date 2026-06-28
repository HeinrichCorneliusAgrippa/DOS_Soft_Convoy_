import json
from typing import List

# Classes the model is trained to detect.
# The index here is the class ID written into YOLO label files.
CLASSES = ['duckie', 'truck', 'sign']

# Images are resized to this square size before training.
IMAGE_SIZE = 416


def convert_labelme_json(json_path: str, img_w: int, img_h: int) -> List[str]:
    with open(json_path) as f:
        data = json.load(f)

    lines = []
    for shape in data["shapes"]:
        label = shape["label"]
        if label not in CLASSES:
            continue

        cls_id = CLASSES.index(label)

        (x1, y1), (x2, y2) = shape["points"]
        xmin, xmax = min(x1, x2), max(x1, x2)
        ymin, ymax = min(y1, y2), max(y1, y2)

        # Scale from original image space to IMAGE_SIZE space
        xmin = xmin * IMAGE_SIZE / img_w
        xmax = xmax * IMAGE_SIZE / img_w
        ymin = ymin * IMAGE_SIZE / img_h
        ymax = ymax * IMAGE_SIZE / img_h

        # Normalize to [0, 1]
        cx = (xmin + xmax) / 2 / IMAGE_SIZE
        cy = (ymin + ymax) / 2 / IMAGE_SIZE
        w = (xmax - xmin) / IMAGE_SIZE
        h = (ymax - ymin) / IMAGE_SIZE

        lines.append(f"{cls_id} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}")

    return lines

