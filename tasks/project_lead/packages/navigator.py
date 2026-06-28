"""Map-aware intersection navigator for the lead bot.

Instead of a hardcoded route list, decide each maneuver the moment the red
stop line fires: locate the bot on the tile grid from its pose, walk forward
to the intersection being entered, compute which exits exist there, and pick
one at random (seedable via config auto_seed).

Needs a live pose — the Godot sim pushes one over the wheel channel. The real
bot has no localization, so callers fall back to the fixed route when
next_step() returns None or no navigator could be built.
"""
import math
import random
from typing import Optional

# Direction indices in world coords (x east, z south): N, E, S, W.
_DIRS = ((0, -1), (1, 0), (0, 1), (-1, 0))

# Tile connectivity at rot=0; a +90 deg Godot rotation maps edge index i -> i-1.
_BASE_CONNS = {
    "straight": (0, 2),
    "curve": (0, 3),
    "cross3": (0, 1, 3),
    "cross": (0, 1, 2, 3),
}
_INTERSECTIONS = ("cross3", "cross")


def load_sim_map(task_name: str = "project_lead") -> Optional[dict]:
    """Parse the task's sim scene into map data. Returns None when the Godot
    project / launcher aren't available (e.g. deployed on the real robot)."""
    try:
        from launcher.config import GODOT_SCENES
        from servers.sim_map import load_map_for_scene
        return load_map_for_scene(GODOT_SCENES.get(task_name, ""))
    except Exception:
        return None


class TopoNavigator:
    def __init__(self, map_data: dict, seed=None):
        self.ts = float(map_data.get("tile_size", 0.6))
        # (col, row) -> (kind, connected edge indices)
        self.tiles = {}
        for t in map_data.get("tiles", []):
            cell = self._cell(t["x"], t["z"])
            k = int(round(t["rot"] / 90.0)) % 4
            conns = tuple((i - k) % 4 for i in _BASE_CONNS[t["kind"]])
            self.tiles[cell] = (t["kind"], conns)
        self._rng = random.Random(seed)

    def _cell(self, x, z):
        return (round(x / self.ts - 0.5), round(z / self.ts - 0.5))

    @staticmethod
    def _heading_dir(heading_rad: float) -> int:
        """Quantize the forward vector fwd = (sin h, cos h) to N/E/S/W."""
        fx, fz = math.sin(heading_rad), math.cos(heading_rad)
        best, bi = -2.0, 0
        for i, (dx, dz) in enumerate(_DIRS):
            dot = fx * dx + fz * dz
            if dot > best:
                best, bi = dot, i
        return bi

    def next_step(self, x: float, z: float, heading_rad: float) -> Optional[str]:
        """Maneuver for the intersection the bot is entering: 'left' | 'right'
        | 'straight', or None when the bot can't be placed on the road graph
        (caller should fall back to its fixed route)."""
        cell = self._cell(x, z)
        d = self._heading_dir(heading_rad)
        # The red line fires on approach: the bot is on the tile before the
        # intersection or already nosing onto it. Walk straight ahead to it.
        for _ in range(3):
            tile = self.tiles.get(cell)
            if tile is None:
                return None
            kind, conns = tile
            if kind in _INTERSECTIONS:
                entry = (d + 2) % 4
                options = [name for rel, name in ((0, "straight"), (1, "right"), (3, "left"))
                           if (d + rel) % 4 != entry and (d + rel) % 4 in conns]
                if not options:
                    return None
                return self._rng.choice(options)
            if kind == "curve":
                return None  # a curve before the line means we're lost
            dx, dz = _DIRS[d]
            cell = (cell[0] + dx, cell[1] + dz)
        return None
