"""Follower-bot perception: lane following + circle-grid back-marker tracking.

No AprilTag/red-line/YOLO (and thus no object_detection dependency). The grid
observation is mapped into the existing LeaderObs shape so the follow controller
needs no changes.
"""
from typing import Optional

import cv2

from tasks.project.packages.marker_grid import MarkerGridTracker
from tasks.project.packages.world_model import LaneObs, LeaderObs, WorldModel
from tasks.visual_lane_servoing.packages.agent import LaneServoingAgent


class FollowPerception:
    def __init__(self, cfg: Optional[dict] = None, lane_config_path: Optional[str] = None):
        cfg = cfg or {}
        self.lane_healthy_min_pixels = int(cfg.get("lane_healthy_min_pixels", 500))
        self.lane = LaneServoingAgent(config_path=lane_config_path)
        self.grid = MarkerGridTracker(cfg=cfg)
        self.last_debug_info: dict = {}

    def update(self, frame_bgr, now: float) -> WorldModel:
        frame_rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        h, w = frame_rgb.shape[:2]

        lane_obs = self._lane_obs(frame_rgb)
        g = self.grid.update(frame_bgr)

        leader = None
        if g is not None:
            cx = g.midpoint[0]
            lateral = float((cx - w / 2) / (w / 2))
            leader = LeaderObs(
                bbox=g.bbox,
                distance_px=g.span_px,
                lateral=lateral,
                score=g.score,
                pair_px=g.span_px,
                source="grid",
                heading=g.heading,
            )

        self.last_debug_info = {
            "leader_source": "grid" if leader else None,
            "led_pair_px": round(g.span_px, 1) if g else None,
            "grid_heading": round(g.heading, 2) if (g and g.heading is not None) else None,
            "apriltag_ids": [],
        }

        return WorldModel(t=now, frame_w=w, frame_h=h, lane=lane_obs,
                          leader=leader, signs=[], red_line=None)

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
