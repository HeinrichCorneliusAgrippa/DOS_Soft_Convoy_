class_name MapMakerExporter
# ─────────────────────────────────────────────────────────────────────────────
# Builds and saves a Godot .tscn scene from a MapData snapshot.
#
# Called from MapMaker like:
#   var err := MapMakerExporter.export_scene(data, map_name, _tile_scenes, _obj_scenes)
#
# Returns "" on success, or a human-readable error string on failure.
# All functions are static — no scene-tree state needed.
# ─────────────────────────────────────────────────────────────────────────────


static func export_scene(
		data:        MapData,
		map_name:    String,
		tile_scenes: Dictionary,
		obj_scenes:  Dictionary) -> String:

	var root      := Node3D.new()
	root.name      = map_name.replace(" ", "_")

	_export_tiles(root, data, tile_scenes)
	_export_ducks(root, data, obj_scenes)
	_export_signs(root, data, obj_scenes)
	_export_bot(root, data)
	_export_npc_path(root, data, obj_scenes)
	_export_lighting(root, data)
	_export_ground(root, data)

	return _save_to_disk(root, map_name)  # packs and frees root


# ── Export sub-steps ──────────────────────────────────────────────────────────

static func _export_tiles(root: Node3D, data: MapData, tile_scenes: Dictionary) -> void:
	var tile_root := _group(root, "Tiles")
	for cell in data.tiles.keys():
		var e: Dictionary  = data.tiles[cell]
		var scene: PackedScene = tile_scenes.get(e["type"])
		if scene == null:
			continue
		var node: Node3D = scene.instantiate()
		node.name               = "Tile_%d_%d" % [cell.x, cell.y]
		var twp                 := data.cell_world_center(cell)
		node.position           = Vector3(twp.x, 0.091, twp.z)
		node.rotation_degrees.y = e["rot"]
		tile_root.add_child(node)
		node.owner = root


static func _export_ducks(root: Node3D, data: MapData, obj_scenes: Dictionary) -> void:
	var duck_root  := _group(root, "Ducks")
	var duck_scene := obj_scenes.get("duck") as PackedScene
	for cell in data.duck_cells.keys():
		if duck_scene == null:
			break
		var rot: int    = data.duck_cells[cell]["rot"]
		var node: Node3D = duck_scene.instantiate()
		node.name               = "Duck_%d_%d" % [cell.x, cell.y]
		var center              := data.cell_world_center(cell)
		var a                   := deg_to_rad(float(rot))
		node.position           = Vector3(
			center.x + data.npc_lane_offset * cos(a),
			0.130,   # road surface 0.091 + sphere radius 0.05 - sphere local offset 0.012
			center.z - data.npc_lane_offset * sin(a)
		)
		node.rotation_degrees.y = rot
		duck_root.add_child(node)
		node.owner = root


static func _export_signs(root: Node3D, data: MapData, obj_scenes: Dictionary) -> void:
	var sign_root := _group(root, "Signs")
	for corner in data.sign_corners.keys():
		var e: Dictionary      = data.sign_corners[corner]
		var scene: PackedScene = obj_scenes.get(e["type"])
		if scene == null:
			continue
		var node: Node3D = scene.instantiate()
		node.name               = "Sign_%d_%d" % [corner.x, corner.y]
		var eswp                := data.corner_world_pos(corner)
		node.position           = Vector3(eswp.x, 0.091, eswp.z)
		node.rotation_degrees.y = e["rot"]
		sign_root.add_child(node)
		node.owner = root


static func _export_bot(root: Node3D, data: MapData) -> void:
	if data.start_cell.x < 0:
		return
	var bot_scene := load("res://scenes/robot/duckie_bot.tscn") as PackedScene
	if bot_scene == null:
		return
	var bot := bot_scene.instantiate() as Node3D
	bot.position           = data.npc_world_pos(data.start_cell, data.start_rot)
	bot.rotation_degrees.y = data.start_rot
	root.add_child(bot)
	bot.owner = root
	# Do NOT call _own_recursive — children belong to the packed scene instance
	# and must not be individually re-owned (causes duplicates in the .tscn file).

	# Third-person follow camera attached to the bot
	var cam := Camera3D.new()
	cam.name             = "TopView"
	cam.current          = true
	cam.position         = Vector3(0.0, 1.0, 0.8)
	cam.rotation_degrees = Vector3(-70.0, 0.0, 0.0)
	bot.add_child(cam)
	cam.owner = root


static func _export_npc_path(root: Node3D, data: MapData, obj_scenes: Dictionary) -> void:
	if data.npc_path_points.size() < 2 and data.npc_cell.x < 0:
		return

	if data.npc_path_points.size() >= 2:
		var path3d := Path3D.new()
		path3d.name = "NPCPath"
		root.add_child(path3d)
		path3d.owner = root

		# Deduplicate consecutive near-identical points that confuse Curve3D
		var curve   := Curve3D.new()
		var last_pt := Vector3(INF, INF, INF)
		for p in data.npc_path_points:
			if p.distance_to(last_pt) > 0.005:
				curve.add_point(p)
				last_pt = p
		if curve.point_count < 2:
			path3d.queue_free()
			return
		path3d.curve = curve

		var pf3d := PathFollow3D.new()
		pf3d.name = "PathFollower"
		pf3d.loop = true
		var pf_script := load("res://scripts/path_follow_3d.gd") as Script
		if pf_script != null:
			pf3d.set_script(pf_script)
			pf3d.set("speed", 0.2)
		path3d.add_child(pf3d)
		pf3d.owner = root

		var npc_scene := obj_scenes.get("npc_car") as PackedScene
		if npc_scene != null:
			var npc := npc_scene.instantiate() as Node3D
			npc.name = "DuckieBot_NPC"
			pf3d.add_child(npc)
			npc.owner = root
	else:
		# No path — place NPC as a static prop at its last-known cell
		var npc_scene := obj_scenes.get("npc_car") as PackedScene
		if npc_scene != null:
			var npc := npc_scene.instantiate() as Node3D
			npc.name               = "DuckieBot_NPC"
			npc.position           = data.npc_world_pos(data.npc_cell, data.npc_rot)
			npc.rotation_degrees.y = data.npc_rot
			root.add_child(npc)
			npc.owner = root


static func _export_lighting(root: Node3D, data: MapData) -> void:
	var cx := data.grid_w * data.tile_size * 0.5
	var cz := data.grid_h * data.tile_size * 0.5

	# Primary sun — strong, near-overhead, slight angle for shadow depth
	var sun := DirectionalLight3D.new()
	sun.name             = "Sun"
	sun.light_energy     = 1.6
	sun.shadow_enabled   = true
	sun.position         = Vector3(cx, 20.0, cz)
	sun.rotation_degrees = Vector3(-75.0, 30.0, 0.0)
	root.add_child(sun)
	sun.owner = root

	# Soft fill from the opposite side — keeps shadows readable
	var fill := DirectionalLight3D.new()
	fill.name             = "FillLight"
	fill.light_energy     = 0.35
	fill.light_color      = Color(0.85, 0.92, 1.0)
	fill.position         = Vector3(cx, 20.0, cz)
	fill.rotation_degrees = Vector3(-30.0, 210.0, 0.0)
	root.add_child(fill)
	fill.owner = root


static func _export_ground(root: Node3D, data: MapData) -> void:
	var gs := Vector2(data.grid_w * data.tile_size * 3.0, data.grid_h * data.tile_size * 3.0)
	var gc := Vector3(data.grid_w * data.tile_size * 0.5, 0.084, data.grid_h * data.tile_size * 0.5)

	# Physics floor — top face at y = 0.091, flush with road tiles
	var ground_body := StaticBody3D.new()
	ground_body.name = "Ground"
	root.add_child(ground_body)   # must be in tree before children get .owner
	ground_body.owner = root

	var box := BoxShape3D.new()
	box.size = Vector3(gs.x, 0.10, gs.y)
	var col  := CollisionShape3D.new()
	col.shape    = box
	col.position = Vector3(
		data.grid_w * data.tile_size * 0.5,
		0.041,   # box centre = 0.041 → top face = 0.091
		data.grid_h * data.tile_size * 0.5
	)
	ground_body.add_child(col)
	col.owner = root

	# Visual ground mesh
	var mi  := MeshInstance3D.new()
	mi.name  = "GroundMesh"
	var pm  := PlaneMesh.new()
	pm.size              = gs
	mi.mesh              = pm
	var mat              := StandardMaterial3D.new()
	mat.albedo_color      = Color(0.14, 0.22, 0.10)
	mat.shading_mode      = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.cull_mode         = BaseMaterial3D.CULL_DISABLED
	mi.material_override  = mat
	mi.position           = gc
	root.add_child(mi)
	mi.owner = root


static func _save_to_disk(root: Node3D, map_name: String) -> String:
	var packed := PackedScene.new()
	var err    := packed.pack(root)
	root.free()   # root no longer needed after packing

	if err != OK:
		return "Pack failed! (%d)" % err

	# ResourceSaver cannot write to res:// at runtime →
	# save to user:// then copy bytes to res://scenes/maps/.
	var tmp_path := "user://_%s_tmp.tscn" % map_name
	err = ResourceSaver.save(packed, tmp_path)
	if err != OK:
		return "Save failed! (%d)" % err

	var dst_dir := ProjectSettings.globalize_path("res://") + "scenes/maps"
	DirAccess.make_dir_recursive_absolute(dst_dir)
	var dst_abs := dst_dir + "/" + map_name + ".tscn"
	var src_abs := ProjectSettings.globalize_path(tmp_path)

	var src_f := FileAccess.open(src_abs, FileAccess.READ)
	if src_f == null:
		return "Read tmp failed!"
	var bytes := src_f.get_buffer(src_f.get_length())
	src_f.close()

	var dst_f := FileAccess.open(dst_abs, FileAccess.WRITE)
	if dst_f == null:
		return "Write failed! Check folder permissions."
	dst_f.store_buffer(bytes)
	dst_f.close()

	DirAccess.remove_absolute(src_abs)
	print("[MapMaker] Exported: " + dst_abs)
	return ""


# ── Shared helpers ────────────────────────────────────────────────────────────

## Creates a named Node3D group container, adds it to root, and sets its owner.
static func _group(root: Node3D, group_name: String) -> Node3D:
	var n := Node3D.new()
	n.name = group_name
	root.add_child(n)
	n.owner = root
	return n


## Recursively sets .owner on all descendants (required for instanced scenes).
static func _own_recursive(node: Node, owner: Node) -> void:
	for child in node.get_children():
		child.owner = owner
		_own_recursive(child, owner)
