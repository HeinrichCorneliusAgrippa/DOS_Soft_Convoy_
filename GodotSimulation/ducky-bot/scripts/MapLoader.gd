class_name MapLoader
# ─────────────────────────────────────────────────────────────────────────────
# MAP LOADER — static utility
# Reads a JSON map saved by MapMaker and spawns tiles + props into any Node3D.
#
# USAGE:
#   MapLoader.load_into(self)                          # loads custom_map.json
#   MapLoader.load_into(self, "user://maps/foo.json")  # loads a specific map
# ─────────────────────────────────────────────────────────────────────────────

const TILE_STRAIGHT := 1
const TILE_CURVE    := 2
const TILE_CROSS    := 3
const TILE_CROSS3   := 4

const _TILE_SCENES: Dictionary = {
	TILE_STRAIGHT: "res://scenes/tiles/tile_straight.tscn",
	TILE_CURVE:    "res://scenes/tiles/tile_curve.tscn",
	TILE_CROSS:    "res://scenes/tiles/tile_cross.tscn",
	TILE_CROSS3:   "res://scenes/tiles/tile_cross3.tscn",
}

const _OBJ_SCENES: Dictionary = {
	"duck":         "res://scenes/objects/obj_duck.tscn",
	"stop_sign":    "res://scenes/objects/obj_stop_sign.tscn",
	"parking_sign": "res://scenes/objects/obj_parking_sign.tscn",
}

const DEFAULT_MAP_PATH := "user://custom_map.json"
const ROOT_NAME        := "LoadedMap"


static func load_into(parent: Node3D, json_path: String = DEFAULT_MAP_PATH) -> int:
	var f := FileAccess.open(json_path, FileAccess.READ)
	if f == null:
		push_warning("[MapLoader] File not found: %s" % json_path)
		return -1

	var data = JSON.parse_string(f.get_as_text())
	f.close()
	if data == null:
		push_error("[MapLoader] Invalid JSON: " + json_path)
		return -1

	clear(parent)

	var root := Node3D.new()
	root.name = ROOT_NAME
	parent.add_child(root)

	var tile_size: float = float(data.get("tile_size", 0.600))

	# ── Road tiles ────────────────────────────────────────────────────────────
	var tile_scenes: Dictionary = {}
	for t in _TILE_SCENES:
		tile_scenes[t] = load(_TILE_SCENES[t])
		if tile_scenes[t] == null:
			push_warning("[MapLoader] Cannot load: " + _TILE_SCENES[t])

	var tile_root := Node3D.new()
	tile_root.name = "Tiles"
	root.add_child(tile_root)

	var count  := 0
	var min_col := INF;  var max_col := -INF
	var min_row := INF;  var max_row := -INF

	for td in data.get("tiles", []):
		var type: int = int(td.get("type", 0))
		var scene: PackedScene = tile_scenes.get(type)
		if scene == null:
			continue
		var col := int(td["col"])
		var row := int(td["row"])
		min_col = min(min_col, col);  max_col = max(max_col, col)
		min_row = min(min_row, row);  max_row = max(max_row, row)
		var node: Node3D = scene.instantiate()
		var bx: float = (col + 0.5) * tile_size
		var bz: float = (row + 0.5) * tile_size
		node.position           = Vector3(bx, 0.091, bz)
		node.rotation_degrees.y = int(td.get("rot", 0))
		tile_root.add_child(node)
		count += 1

	# ── Props ──────────────────────────────────────────────────────────────────
	var obj_scenes: Dictionary = {}
	for t in _OBJ_SCENES:
		obj_scenes[t] = load(_OBJ_SCENES[t])
		if obj_scenes[t] == null:
			push_warning("[MapLoader] Cannot load: " + _OBJ_SCENES[t])

	var obj_root := Node3D.new()
	obj_root.name = "Objects"
	root.add_child(obj_root)

	# Ducks — cell center positions
	for dd in data.get("ducks", []):
		var scene: PackedScene = obj_scenes.get("duck")
		if scene == null:
			continue
		var node: Node3D = scene.instantiate()
		node.position           = Vector3((int(dd["col"]) + 0.5) * tile_size, 0.172, (int(dd["row"]) + 0.5) * tile_size)
		node.rotation_degrees.y = int(dd.get("rot", 0))
		obj_root.add_child(node)

	# Signs — corner positions (cx * tile_size, cy * tile_size)
	for sd in data.get("signs", []):
		var type: String = str(sd.get("type", ""))
		var scene: PackedScene = obj_scenes.get(type)
		if scene == null:
			continue
		var node: Node3D = scene.instantiate()
		node.position           = Vector3(int(sd["cx"]) * tile_size, 0.091, int(sd["cy"]) * tile_size)
		node.rotation_degrees.y = int(sd.get("rot", 0))
		obj_root.add_child(node)

	# ── Map bounds (used by MapAutoLoader to fit the camera) ──────────────────
	if count > 0:
		var sp_pre = data.get("start_point", null)
		if sp_pre != null:
			var sc := int(sp_pre["col"]);  var sr := int(sp_pre["row"])
			min_col = min(min_col, sc);  max_col = max(max_col, sc)
			min_row = min(min_row, sr);  max_row = max(max_row, sr)
		parent.set_meta("_map_bounds", {
			"min": Vector3(min_col * tile_size, 0.0, min_row * tile_size),
			"max": Vector3((max_col + 1) * tile_size, 0.0, (max_row + 1) * tile_size),
		})

	# ── Start point ───────────────────────────────────────────────────────────
	var sp = data.get("start_point", null)
	if sp != null:
		var wx: float = (int(sp["col"]) + 0.5) * tile_size
		var wz: float = (int(sp["row"]) + 0.5) * tile_size

		# Store position + rotation as metadata so MapAutoLoader can move the bot there
		parent.set_meta("_map_start_point", Vector3(wx, 0.0, wz))
		parent.set_meta("_map_start_rot", int(sp.get("rot", 0)))

		print("[MapLoader] ★ Start point → (%.4f, 0, %.4f)  [col %d, row %d]" % [
			wx, wz, int(sp["col"]), int(sp["row"])
		])

	print("[MapLoader] Spawned %d tiles from %s" % [count, json_path])
	return count


static func clear(parent: Node3D) -> void:
	var existing := parent.get_node_or_null(ROOT_NAME)
	if existing:
		existing.queue_free()
	for m in ["_map_start_point", "_map_bounds"]:
		if parent.has_meta(m):
			parent.remove_meta(m)
