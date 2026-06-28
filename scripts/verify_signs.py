#!/usr/bin/env python3
"""End-to-end check that AprilTag signs turn into the right bot actions.

No bot or hardware needed: it renders real tag36h11 markers for the STOP / SLOW
IDs from config/project_lead_config.yaml, runs them through the SAME SignDetector
the lead uses, then drives the SAME LeadFSM and prints what the bot would do
frame-by-frame.

    python3 scripts/verify_signs.py

Expected:
  - SLOW tag in view  -> SLOW_ZONE  (speed = cruise * slow_factor), then resumes.
  - near STOP tag      -> STOP_AT_SIGN (speed 0) for ~stop_tag_halt_s, then resumes.
"""
import os
import sys

import cv2
import numpy as np
import yaml

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tasks.project.packages.signs import SignDetector, _APRILTAG_DICTS
from tasks.project.packages.world_model import LaneObs, WorldModel
from tasks.project_lead.packages.fsm import LeadFSM

CFG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "project_lead_config.yaml")
FRAME_W, FRAME_H = 640, 480


def load_cfg():
    with open(CFG_PATH) as f:
        return yaml.safe_load(f) or {}


def render_tag(cfg, tag_id, tag_px):
    """A FRAME_HxFRAME_W gray frame with one centered AprilTag of the given size."""
    aruco = cv2.aruco
    dict_name = _APRILTAG_DICTS.get(str(cfg.get("apriltag_family", "tag36h11")).lower(),
                                    "DICT_APRILTAG_36h11")
    ad = aruco.getPredefinedDictionary(getattr(aruco, dict_name))
    if hasattr(aruco, "generateImageMarker"):
        marker = aruco.generateImageMarker(ad, tag_id, tag_px)
    else:
        marker = aruco.drawMarker(ad, tag_id, tag_px)
    frame = np.full((FRAME_H, FRAME_W), 255, dtype=np.uint8)
    y0 = (FRAME_H - tag_px) // 2
    x0 = (FRAME_W - tag_px) // 2
    frame[y0:y0 + tag_px, x0:x0 + tag_px] = marker
    return frame


def healthy_lane():
    return LaneObs(steering_suggestion=0.0, base_speed_suggestion=0.3,
                   lane_pixels=1500, is_curve=False, healthy=True)


def make_wm(t, signs):
    return WorldModel(t=t, frame_w=FRAME_W, frame_h=FRAME_H, lane=healthy_lane(),
                      leader=None, signs=signs, red_line=None)


def step_for(label, det, fsm, frame, t0, n=8, dt=0.1, with_tag_frames=1):
    """Run n FSM frames; the tag is only visible for the first with_tag_frames."""
    print(f"\n=== {label} ===")
    blank = np.full((FRAME_H, FRAME_W), 255, dtype=np.uint8)
    for i in range(n):
        t = t0 + i * dt
        g = frame if i < with_tag_frames else blank
        signs = det.detect(g)
        wm = make_wm(t, signs)
        d = fsm.step(wm)
        tags = det.last_tag_ids
        kinds = [s.kind for s in signs]
        print(f"  t={t:4.1f}  tags={tags}  signs={kinds}  "
              f"-> state={d.state_name:13s} speed={d.base_speed:.2f}")
    return t0 + n * dt


def main():
    cfg = load_cfg()
    stop_ids = cfg.get("apriltag_stop_ids")
    slow_ids = cfg.get("apriltag_slow_ids")
    print(f"config: family={cfg.get('apriltag_family')}  "
          f"STOP ids={stop_ids}  SLOW ids={slow_ids}")
    print(f"        cruise={cfg.get('cruise_speed')}  slow_factor={cfg.get('slow_factor')}  "
          f"stop_tag_halt_s={cfg.get('stop_tag_halt_s')}  "
          f"near_area_frac={cfg.get('stop_tag_near_area_frac')}")

    det = SignDetector(cfg)
    if det._detector is None:
        print("\nFAIL: cv2.aruco detector unavailable — install opencv-contrib-python.")
        return 1

    # --- detection sanity: do the configured tags decode at all? ---
    print("\n--- detection check (centered tag, 220px) ---")
    for label, ids in (("STOP", stop_ids), ("SLOW", slow_ids)):
        tid = int(ids[0])
        det.detect(render_tag(cfg, tid, 220))
        ok = tid in det.last_tag_ids
        print(f"  {label} id={tid}: decoded={det.last_tag_ids}  {'OK' if ok else 'MISS'}")

    # --- behavior check: SLOW then STOP, then confirm it resumes ---
    fsm = LeadFSM(cfg)
    t = 0.0
    # A near tag: bbox must be >= near_area_frac of the frame. 220px tag on a
    # 640x480 frame = 220*220/(640*480) ~ 0.157 >> 0.04, so STOP halts directly.
    slow_frame = render_tag(cfg, int(slow_ids[0]), 220)
    stop_frame = render_tag(cfg, int(stop_ids[0]), 220)

    t = step_for("SLOW sign in view (expect SLOW_ZONE ~1.5s, then resume cruise)",
                 det, fsm, slow_frame, t, n=20)
    # gap so the slow cooldown clears before the STOP test
    t += 5.0
    t = step_for("Near STOP sign (expect STOP_AT_SIGN ~1s, then resume cruise)",
                 det, fsm, stop_frame, t, n=16)
    print("\nDone. Read the state column above to confirm the actions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
