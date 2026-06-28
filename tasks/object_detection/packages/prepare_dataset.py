import os
import sys
import random
import cv2

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from tasks.object_detection.packages.dataset_activity import (
    convert_labelme_json, IMAGE_SIZE, CLASSES
)

TRAIN_SPLIT  = 0.8
RANDOM_SEED  = 42

ROOT       = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
RAW_DIR    = os.path.join(ROOT, 'tasks', 'object_detection', 'dataset', 'raw')
OUT_DIR    = os.path.join(ROOT, 'tasks', 'object_detection', 'dataset')


def main():
    image_files = [f for f in os.listdir(RAW_DIR)
                   if f.lower().endswith(('.jpg', '.jpeg', '.png'))]

    entries = []
    for img_file in sorted(image_files):
        base      = os.path.splitext(img_file)[0]
        json_path = os.path.join(RAW_DIR, base + '.json')
        img_path  = os.path.join(RAW_DIR, img_file)
        entries.append((img_path, json_path if os.path.exists(json_path) else None))

    print(f"Found {len(entries)} images ({sum(1 for _, j in entries if j)} labeled)")

    random.seed(RANDOM_SEED)
    random.shuffle(entries)
    split = int(len(entries) * TRAIN_SPLIT)
    splits = {'train': entries[:split], 'val': entries[split:]}

    for subset, items in splits.items():
        img_out = os.path.join(OUT_DIR, subset, 'images')
        lbl_out = os.path.join(OUT_DIR, subset, 'labels')
        os.makedirs(img_out, exist_ok=True)
        os.makedirs(lbl_out, exist_ok=True)

        for img_path, json_path in items:
            img = cv2.imread(img_path)
            if img is None:
                print(f"  Skipping unreadable image: {img_path}")
                continue

            h, w = img.shape[:2]
            img_resized = cv2.resize(img, (IMAGE_SIZE, IMAGE_SIZE))

            base = os.path.splitext(os.path.basename(img_path))[0]
            cv2.imwrite(os.path.join(img_out, base + '.jpg'), img_resized)

            lines = convert_labelme_json(json_path, w, h) if json_path else []

            with open(os.path.join(lbl_out, base + '.txt'), 'w') as f:
                f.write('\n'.join(lines))

        print(f"  {subset}: {len(items)} images")

    print(f"\nDone! Dataset ready at:")
    print(f"  {os.path.join(OUT_DIR, 'train')}")
    print(f"  {os.path.join(OUT_DIR, 'val')}")


if __name__ == '__main__':
    main()
