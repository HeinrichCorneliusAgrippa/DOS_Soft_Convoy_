from dataclasses import dataclass, field
from typing import List, Optional, Tuple

SignKind = str
Bbox = Tuple[int, int, int, int]
LeaderSource = str  # "yolo" | "led" | "fused" | "grid"


@dataclass
class LeaderObs:
    bbox: Bbox
    distance_px: float
    lateral: float
    score: float
    pair_px: Optional[float] = None
    source: LeaderSource = "yolo"
    # Best-effort heading of the leader's back pattern, signed radians, +ve when
    # the leader is yawed to its right (turning right) as seen by the follower.
    # None when unavailable (no camera intrinsics / symmetric-grid ambiguity).
    heading: Optional[float] = None


@dataclass
class RedLineObs:
    """A red stop-line spanning the lane ahead of the lead bot."""
    present: bool
    area_px: float        # red pixels inside the detection band
    width_frac: float     # widest red component as a fraction of frame width
    dist_proxy: float     # band-row centre of the line, 0=far(top of band) .. 1=near(bottom)


@dataclass
class SignObs:
    kind: SignKind
    bbox: Bbox
    score: float


@dataclass
class LaneObs:
    steering_suggestion: float
    base_speed_suggestion: float
    lane_pixels: int
    is_curve: bool
    healthy: bool


@dataclass
class WorldModel:
    t: float
    frame_w: int
    frame_h: int
    lane: LaneObs
    leader: Optional[LeaderObs] = None       # follower: the lead's back pattern
    signs: List[SignObs] = field(default_factory=list)  # lead: AprilTag traffic signs
    red_line: Optional[RedLineObs] = None    # lead: stop-line / intersection cue
    detector_ready: bool = True
