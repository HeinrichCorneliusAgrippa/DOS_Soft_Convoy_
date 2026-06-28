"""Lead-bot perception: lane following + AprilTag traffic signs + red stop-line.

The lead tracks no leader, so there is no LED/YOLO/circle-grid here (and thus no
object_detection dependency). Produces a WorldModel with lane/signs/red_line.
"""
import os
from typing import Optional

import cv2

from tasks.project.packages.red_line import RedLineDetector
from tasks.project.packages.signs import SignDetector
from tasks.project.packages.world_model import LaneObs, WorldModel
from tasks.visual_lane_servoing.packages.agent import LaneServoingAgent


class LeadPerception:
    def __init__(self, cfg: Optional[dict] = None,
                 lane_config_path: Optional[str] = None,
                 stopline_hsv_path: Optional[str] = None):
        cfg = cfg or {}
        self.lane_healthy_min_pixels = int(cfg.get("lane_healthy_min_pixels", 500))
        self.lane = LaneServoingAgent(config_path=lane_config_path)
        self.signs = SignDetector(cfg)
        self.red = RedLineDetector(cfg, hsv_path=stopline_hsv_path)
        self.last_debug_info: dict = {}

    def update(self, frame_bgr, now: float) -> WorldModel:
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        h, w = frame_rgb.shape[:2]
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

        lane_obs = self._lane_obs(frame_rgb)
        signs = self.signs.detect(gray)
        red = self.red.detect(frame_bgr)

        self.last_debug_info = {
            "apriltag_ids": list(self.signs.last_tag_ids),
            "apriltag_rejected": int(getattr(self.signs, "last_rejected", 0)),
            "red_line": (round(red.width_frac, 2), round(red.dist_proxy, 2)) if red.present else None,
            "leader_source": None,
            "led_pair_px": None,
        }

        return WorldModel(t=now, frame_w=w, frame_h=h, lane=lane_obs,
                          leader=None, signs=signs, red_line=red)

    def _lane_obs(self, frame_rgb) -> LaneObs:
        left, right = self.lane.compute_commands(frame_rgb)
        debug = self.lane.last_debug_info
        lane_pixels = int(debug.get("total_lane_pixels", 0))
        return LaneObs(
            steering_suggestion=(right - left) / 2.0,
            base_speed_suggestion=(left + right) / 2.0,
            lane_pixels=lane_pixels,
            is_curve=bool(debug.get("is_curve", False)),
            healthy=lane_pixels >= self.lane_healthy_min_pixels,
        )

    def reset_lane(self) -> None:
        self.lane.reset()
