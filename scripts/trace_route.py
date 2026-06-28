#!/usr/bin/env python3
"""Trace the lead's fixed route over the KIU map graph — no sim required.

Fixed-mode routes are applied BLINDLY (one token per red-line intersection),
so they only work if they match the intersections actually hit from the spawn.
This drives the tile graph from a given spawn cell + heading, applying the
route tokens at each intersection, and reports whether every turn is legal.

    python3 scripts/trace_route.py

Edit SPAWN_CELL / SPAWN_DIR below to match the Godot spawn you're testing.
"""
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import yaml

# N, E, S, W in world coords (x east, z south).
_DIRS = ((0, -1), (1, 0), (0, 1), (-1, 0))
_NAME = ("N", "E", "S", "W")
_BASE_CONNS = {
    "straight": (0, 2),
    "curve": (0, 3),
    "cross3": (0, 1, 3),
    "cross": (0, 1, 2, 3),
}
_INTERSECTIONS = ("cross3", "cross")
_REL = {"straight": 0, "right": 1, "left": 3}

# --- spawn under test (bottom-left, opposite lane => heading SOUTH) ---
SPAWN_CELL = (1, 10)   # col, row  (current json spawn is (1,10))
SPAWN_DIR = 2          # 0=N 1=E 2=S 3=W   (current json spawn heads N=0)


def load_map():
    p = os.path.join(os.path.dirname(__file__), "..", "config", "kiu_map.json")
    return json.load(open(p))


def build_cells(m):
    ts = float(m.get("tile_size", 0.6))
    cells = {}
    for t in m["tiles"]:
        col = round(t["x"] / ts - 0.5)
        row = round(t["z"] / ts - 0.5)
        k = int(round(t["rot"] / 90.0)) % 4
        conns = tuple((i - k) % 4 for i in _BASE_CONNS[t["kind"]])
        cells[(col, row)] = (t["kind"], conns)
    return cells


def load_route():
    p = os.path.join(os.path.dirname(__file__), "..", "config", "project_lead_config.yaml")
    return [str(s).lower() for s in yaml.safe_load(open(p)).get("route", [])]


def trace(cells, route, cell, d, max_steps=400):
    idx = 0
    n_inter = 0
    ok = True
    print(f"spawn: cell={cell} heading={_NAME[d]}")
    for _ in range(max_steps):
        tile = cells.get(cell)
        if tile is None:
            print(f"  cell {cell}: OFF-MAP — spawn/heading leaves the road graph"); return False
        kind, conns = tile
        entry = (d + 2) % 4
        if entry not in conns:
            print(f"  cell {cell} ({kind}): entered from {_NAME[entry]} but tile only "
                  f"connects {[_NAME[c] for c in conns]} — wrong lane/heading"); return False

        if kind in _INTERSECTIONS:
            n_inter += 1
            if idx >= len(route):
                print(f"  intersection #{n_inter} {cell} ({kind}): route EXHAUSTED "
                      f"(defaults to stop)"); return ok
            tok = route[idx]; idx += 1
            avail = [_NAME[(d + r) % 4] for nm, r in _REL.items()
                     if (d + r) % 4 in conns and (d + r) % 4 != entry]
            if tok == "stop":
                print(f"  intersection #{n_inter} {cell} ({kind}) head {_NAME[d]}: "
                      f"STOP (final halt). exits available: {avail}")
                return ok
            ex = (d + _REL[tok]) % 4
            legal = ex in conns and ex != entry
            mark = "OK " if legal else "ILLEGAL"
            print(f"  intersection #{n_inter} {cell} ({kind}) head {_NAME[d]}: "
                  f"{tok:8s} -> exit {_NAME[ex]}  [{mark}]  (legal exits: {avail})")
            if not legal:
                ok = False
            d = ex
        else:
            exits = [e for e in conns if e != entry]
            if len(exits) != 1:
                print(f"  cell {cell} ({kind}): ambiguous exits {exits}"); return False
            d = exits[0]
        dx, dz = _DIRS[d]
        cell = (cell[0] + dx, cell[1] + dz)
    print("  (max steps reached)")
    return ok


def main():
    m = load_map()
    cells = build_cells(m)
    route = load_route()
    print(f"route: {route}\n")
    ok = trace(cells, route, SPAWN_CELL, SPAWN_DIR)
    print(f"\n{'ALL TURNS LEGAL' if ok else 'ROUTE HAS ILLEGAL TURNS — see above'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
