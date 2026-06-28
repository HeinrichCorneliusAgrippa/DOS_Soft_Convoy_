class_name MapData
# ─────────────────────────────────────────────────────────────────────────────
# Serialisable snapshot of all map state.
#
# Owns three things:
#   1. The data fields (tiles, ducks, signs, start, NPC).
#   2. Geometry helpers so the exporter doesn't need MapMaker's constants.
#   3. to_dict / from_dict for JSON persistence.
#
# MapMaker creates a MapData via _build_map_data() and passes it to
# MapMakerExporter and MapMakerFileIO without those classes knowing about
# MapMaker at all.
# ─────────────────────────────────────────────────────────────────────────────


# ── Data fields ───────────────────────────────────────────────────────────────

var tile_size:        float    = 0.600
var npc_lane_offset:  float    = 0.132   # tile_size * 0.22
var grid_w:           int      = 16
var grid_h:           int      = 16

var tiles:            Dictionary = {}   # Vector2i → {type:int, rot:int}
var duck_cells:       Dictionary = {}   # Vector2i → {rot:int}
var sign_corners:     Dictionary = {}   # Vector2i → {type:String, rot:int}
var start_cell:       Vector2i   = Vector2i(-1, -1)
var start_rot:        int        = 0
var npc_cell:         Vector2i   = Vector2i(-1, -1)
var npc_rot:          int        = 0
var npc_path_points:  Array      = []   # Array[Vector3]


# ── Geometry helpers ──────────────────────────────────────────────────────────

func cell_world_center(c: Vector2i) -> Vector3:
	return Vector3((c.x + 0.5) * tile_size, 0.0, (c.y + 0.5) * tile_size)

func corner_world_pos(c: Vector2i) -> Vector3:
	return Vector3(c.x * tile_size, 0.0, c.y * tile_size)

func npc_world_pos(cell: Vector2i, rot_deg: int) -> Vector3:
	var center := cell_world_center(cell)
	var a      := deg_to_rad(float(rot_deg))
	return Vector3(
		center.x + npc_lane_offset * cos(a),
		0.155,   # tile surface 0.091 + bot half-height 0.059 + 5 mm clearance
		center.z - npc_lane_offset * sin(a)
	)


# ── Serialisation ─────────────────────────────────────────────────────────────

func to_dict(map_name: String) -> Dictionary:
	var d := {
		"name": map_name, "tile_size": tile_size,
		"grid_w": grid_w, "grid_h": grid_h,
		"tiles": [], "ducks": [], "signs": [],
	}
	for cell in tiles.keys():
		var e: Dictionary = tiles[cell]
		d["tiles"].append({"col": cell.x, "row": cell.y, "type": e["type"], "rot": e["rot"]})
	for cell in duck_cells.keys():
		d["ducks"].append({"col": cell.x, "row": cell.y, "rot": duck_cells[cell]["rot"]})
	for corner in sign_corners.keys():
		var e: Dictionary = sign_corners[corner]
		d["signs"].append({"cx": corner.x, "cy": corner.y, "type": e["type"], "rot": e["rot"]})
	if start_cell.x >= 0:
		d["start_point"] = {"col": start_cell.x, "row": start_cell.y, "rot": start_rot}
	if npc_cell.x >= 0:
		d["npc_car"] = {"col": npc_cell.x, "row": npc_cell.y, "rot": npc_rot}
	if npc_path_points.size() > 0:
		var wps: Array = []
		for p in npc_path_points:
			wps.append({"x": p.x, "z": p.z})
		d["npc_path"] = wps
	return d


static func from_dict(d: Dictionary) -> MapData:
	var data             := MapData.new()
	data.tile_size        = float(d.get("tile_size", 0.600))
	data.grid_w           = int(d.get("grid_w",     16))
	data.grid_h           = int(d.get("grid_h",     16))
	data.npc_lane_offset  = data.tile_size * 0.22

	for td in d.get("tiles", []):
		data.tiles[Vector2i(int(td["col"]), int(td["row"]))] = {
			"type": int(td["type"]), "rot": int(td["rot"])
		}
	for dd in d.get("ducks", []):
		data.duck_cells[Vector2i(int(dd["col"]), int(dd["row"]))] = {"rot": int(dd["rot"])}
	for sd in d.get("signs", []):
		data.sign_corners[Vector2i(int(sd["cx"]), int(sd["cy"]))] = {
			"type": str(sd["type"]), "rot": int(sd["rot"])
		}
	var sp = d.get("start_point", null)
	if sp != null:
		data.start_cell = Vector2i(int(sp["col"]), int(sp["row"]))
		data.start_rot  = int(sp.get("rot", 0))
	var nc = d.get("npc_car", null)
	if nc != null:
		data.npc_cell = Vector2i(int(nc["col"]), int(nc["row"]))
		data.npc_rot  = int(nc.get("rot", 0))
	for p in d.get("npc_path", []):
		data.npc_path_points.append(Vector3(float(p["x"]), 0.091, float(p["z"])))

	return data
