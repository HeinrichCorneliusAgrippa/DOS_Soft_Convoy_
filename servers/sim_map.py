"""Parse a Godot map .tscn into the data the convoy sim UI needs.

The map scenes place tile/duck/sign/robot instances with Transform3D rows
serialized as 12 floats: 9 basis values row by row, then the origin. All map
objects are rotated about Y only, so the yaw comes out of basis row 0:
theta = atan2(m[2], m[0]). Tile rotations snap to 90 deg.

Road connectivity is derived from the tile kind + rotation. Base (rot=0)
connections, verified against the tile textures / placed maps:
  straight: N-S   curve: N-W   cross3: N-E-W   cross: N-E-S-W
A +90 deg Godot rotation maps E->N->W->S->E (CCW seen from above, +X east /
+Z south), which on the grid means each connection direction rotates the
same way.
"""
import json
import math
import os
import re

_EXT_RE = re.compile(r'^\[ext_resource\s+[^\]]*path="([^"]+)"[^\]]*id="([^"]+)"', re.M)
_NODE_RE = re.compile(
    r'^\[node\s+name="([^"]+)"[^\]]*?instance=ExtResource\("([^"]+)"\)\s*\]\s*\n'
    r'(?:transform = Transform3D\(([^)]*)\))?',
    re.M,
)
_CURVE_RE = re.compile(r'"points":\s*PackedVector3Array\(([^)]*)\)')

# Substring of the ext_resource path -> object kind. Order matters: the first
# match wins (cross3 before cross, bot_npc before bot).
_KIND_BY_PATH = [
    ("tile_straight", "straight"),
    ("tile_curve", "curve"),
    ("tile_cross3", "cross3"),
    ("tile_cross", "cross"),
    ("DuckieRagdool", "duck"),
    ("obj_duck", "duck"),
    ("obj_stop_sign", "sign_stop"),
    ("obj_parking_sign", "sign_parking"),
    ("duckie_bot_npc", "npc"),
    ("duckie_bot", "bot"),
]

TILE_KINDS = ("straight", "curve", "cross3", "cross")
TILE_SIZE = 0.6  # meters (MapData.gd)


def _classify(path):
    for needle, kind in _KIND_BY_PATH:
        if needle in path:
            return kind
    return None


def _yaw_deg(m):
    """Yaw about Y from a serialized basis, snapped to the nearest degree."""
    return round(math.degrees(math.atan2(m[2], m[0])))


def _heading_rad(m):
    """Heading of the forward (-basis.z) vector, same convention as the pose
    Godot reports: atan2(fwd_x, fwd_z)."""
    return math.atan2(-m[2], -m[8])


def parse_map_tscn(tscn_path):
    """Return a JSON-serializable description of a map scene.

    {
      'tile_size': 0.6,
      'tiles':  [{'kind', 'x', 'z', 'rot'}],          # rot in {0, 90, 180, 270}
      'ducks':  [{'x', 'z'}],
      'signs':  [{'kind', 'x', 'z'}],
      'bot':    {'x', 'z', 'heading'} | None,         # networked robot spawn
      'npc_path': [[x, z], ...] | None,               # leader path polyline
      'bounds': {'min_x', 'min_z', 'max_x', 'max_z'}  # tile extent incl. borders
    }
    """
    with open(tscn_path, encoding="utf-8") as f:
        text = f.read()

    paths_by_id = {rid: path for path, rid in _EXT_RE.findall(text)}

    tiles, ducks, signs = [], [], []
    bot = None
    for _name, rid, transform in _NODE_RE.findall(text):
        kind = _classify(paths_by_id.get(rid, ""))
        if kind is None:
            continue
        m = [float(v) for v in transform.split(",")] if transform else \
            [1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0]
        x, z = m[9], m[11]
        if kind in TILE_KINDS:
            tiles.append({"kind": kind, "x": x, "z": z, "rot": _yaw_deg(m) % 360})
        elif kind == "duck":
            ducks.append({"x": x, "z": z})
        elif kind.startswith("sign"):
            signs.append({"kind": kind, "x": x, "z": z})
        elif kind == "bot":
            bot = {"x": x, "z": z, "heading": _heading_rad(m)}
        # 'npc' position is path-driven; the path polyline below covers it.

    npc_path = None
    curve = _CURVE_RE.search(text)
    if curve:
        vals = [float(v) for v in curve.group(1).split(",")]
        # Curve3D points serialize as (in_handle, out_handle, position) vec3
        # triplets: the position is floats 6..8 of every group of 9.
        npc_path = [[vals[i + 6], vals[i + 8]] for i in range(0, len(vals) - 8, 9)]

    if tiles:
        h = TILE_SIZE / 2.0
        bounds = {
            "min_x": min(t["x"] for t in tiles) - h,
            "max_x": max(t["x"] for t in tiles) + h,
            "min_z": min(t["z"] for t in tiles) - h,
            "max_z": max(t["z"] for t in tiles) + h,
        }
    else:
        bounds = {"min_x": 0.0, "max_x": 1.0, "min_z": 0.0, "max_z": 1.0}

    return {
        "tile_size": TILE_SIZE,
        "tiles": tiles,
        "ducks": ducks,
        "signs": signs,
        "bot": bot,
        "npc_path": npc_path,
        "bounds": bounds,
    }


def load_map_for_scene(scene_res_path, project_root=None):
    """Resolve a res:// scene path against the Godot project and parse it.
    Returns None (rather than raising) when the scene can't be read, so a
    missing map degrades to a UI without the map panel."""
    if project_root is None:
        project_root = os.path.normpath(os.path.join(os.path.dirname(__file__), ".."))
    rel = scene_res_path.replace("res://", "")
    tscn = os.path.join(project_root, "GodotSimulation", "ducky-bot", rel)
    try:
        return parse_map_tscn(tscn)
    except Exception as e:
        print(f"[sim_map] could not parse {tscn}: {e}")
        return None


if __name__ == "__main__":
    import sys
    print(json.dumps(parse_map_tscn(sys.argv[1]), indent=2))
