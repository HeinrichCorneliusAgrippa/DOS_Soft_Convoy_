class_name IndicatorFactory
# ─────────────────────────────────────────────────────────────────────────────
# Static factory for every flat ground-level visual in the MapMaker editor:
#   • sign overlays (coloured disc + directional arrow)
#   • NPC path waypoint discs
#   • NPC path arrows between waypoints
#   • ghost material for placement previews
#
# All functions are static and return Node3D — no scene-tree state needed.
# ─────────────────────────────────────────────────────────────────────────────


## Unshaded, double-sided material.  Alpha < 1 enables transparency automatically.
static func flat_mat(color: Color) -> StandardMaterial3D:
	var mat := StandardMaterial3D.new()
	mat.albedo_color = color
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	mat.cull_mode    = BaseMaterial3D.CULL_DISABLED
	if color.a < 0.999:
		mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
	return mat


## Recursively makes every MeshInstance3D in a subtree semi-transparent.
## Used to render the placement ghost preview.
static func apply_ghost_material(node: Node) -> void:
	if node is MeshInstance3D:
		var mi := node as MeshInstance3D
		if mi.mesh != null:
			for i in range(mi.mesh.get_surface_count()):
				var orig: Material = mi.get_surface_override_material(i)
				if orig == null:
					orig = mi.mesh.surface_get_material(i)
				var mat: StandardMaterial3D
				if orig is StandardMaterial3D:
					mat = orig.duplicate() as StandardMaterial3D
					mat.albedo_color.a = 0.5
				else:
					mat = StandardMaterial3D.new()
					mat.albedo_color = Color(0.8, 0.8, 0.8, 0.5)
				mat.transparency = BaseMaterial3D.TRANSPARENCY_ALPHA
				mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
				mat.cull_mode    = BaseMaterial3D.CULL_DISABLED
				mi.set_surface_override_material(i, mat)
	for child in node.get_children():
		apply_ghost_material(child)


## Top-down overlay for a road sign: coloured disc + white directional arrow.
## rot_y rotates the whole indicator so the arrow always points toward the sign face.
static func make_sign_indicator(world_pos: Vector3, type: String, rot_y: int) -> Node3D:
	var root := Node3D.new()
	root.position         = world_pos
	root.rotation_degrees = Vector3(0.0, float(rot_y), 0.0)

	var sign_color := Color(0.92, 0.07, 0.07) if type == "stop_sign" \
	                                           else Color(0.07, 0.18, 0.88)

	root.add_child(_plane_mi(Vector2(0.14, 0.14), sign_color, 0.010))

	# Arrow stem — offset along local +Z (= sign face direction)
	var stem := _plane_mi(Vector2(0.04, 0.18), Color(1.0, 1.0, 1.0, 0.95), 0.012)
	stem.position.z = 0.16
	root.add_child(stem)

	# Wider arrowhead so the direction is unmistakable
	var tip := _plane_mi(Vector2(0.10, 0.06), Color(1.0, 1.0, 1.0, 0.95), 0.012)
	tip.position.z = 0.28
	root.add_child(tip)

	return root


## Coloured flat disc with a sequence number — marks one NPC path waypoint.
static func make_waypoint_disc(world_pos: Vector3, col: Color, number: int, tile_size: float) -> Node3D:
	var root := Node3D.new()
	root.position = world_pos

	var side := tile_size * 0.13
	root.add_child(_plane_mi(Vector2(side, side), col, 0.002))

	var lbl := Label3D.new()
	lbl.text             = str(number)
	lbl.pixel_size       = 0.0035
	lbl.modulate         = Color.WHITE
	lbl.outline_size     = 4
	lbl.outline_modulate = Color.BLACK
	lbl.billboard        = BaseMaterial3D.BILLBOARD_DISABLED
	lbl.position         = Vector3(0.0, 0.010, 0.0)
	lbl.rotation_degrees = Vector3(-90.0, 0.0, 0.0)
	root.add_child(lbl)

	return root


## Flat ground arrow from point a to point b, with a small arrowhead near b.
static func make_path_arrow(a: Vector3, b: Vector3, tile_size: float) -> Node3D:
	var root := Node3D.new()

	var dx     := b.x - a.x
	var dz     := b.z - a.z
	var length := sqrt(dx * dx + dz * dz)
	if length < 0.001:
		return root

	var angle_y := atan2(dx, dz)
	var mid     := Vector3((a.x + b.x) * 0.5, a.y + 0.001, (a.z + b.z) * 0.5)

	# Stem — gap at each end keeps waypoint discs fully visible
	var stem_len: float = max(0.01, length - tile_size * 0.38)
	var stem := _plane_mi(Vector2(0.025, stem_len), Color(1.0, 1.0, 1.0, 0.85), 0.0)
	stem.position   = mid
	stem.rotation.y = angle_y
	root.add_child(stem)

	# Arrowhead near the destination disc
	var tip_offset := length * 0.5 - tile_size * 0.22
	var tip_pos    := Vector3(
		mid.x + sin(angle_y) * tip_offset,
		mid.y + 0.001,
		mid.z + cos(angle_y) * tip_offset
	)
	var tip := _plane_mi(Vector2(0.10, 0.10), Color(1.0, 1.0, 1.0, 0.9), 0.0)
	tip.position   = tip_pos
	tip.rotation.y = angle_y
	root.add_child(tip)

	return root


# ── private ───────────────────────────────────────────────────────────────────

## PlaneMesh MeshInstance3D with a flat material; y_offset shifts it above ground.
static func _plane_mi(size: Vector2, color: Color, y_offset: float) -> MeshInstance3D:
	var mi  := MeshInstance3D.new()
	var pm  := PlaneMesh.new()
	pm.size  = size
	mi.mesh  = pm
	mi.material_override = flat_mat(color)
	mi.position.y = y_offset
	return mi
