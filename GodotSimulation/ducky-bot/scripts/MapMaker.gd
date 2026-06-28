extends Node3D
# ─────────────────────────────────────────────────────────────────────────────
# MAP MAKER  v7
#
# This script is the interactive editor only — placement, hover, input, UI.
# Everything else lives in purpose-built companion scripts:
#
#   MapData.gd             — serialisable data container + geometry helpers
#   MapMakerFileIO.gd      — save / load / list / delete JSON map files
#   MapMakerExporter.gd    — build and write .tscn scene files
#   IndicatorFactory.gd    — flat ground overlays (discs, arrows, ghost)
#   NpcPathVariants.gd     — NPC path arc mathematics
#
# Placement grids:
#   CELL grid  (16×16)  — tiles, ducks, start, NPC car
#   CORNER grid (17×17) — signs at road-edge intersections
# ─────────────────────────────────────────────────────────────────────────────


# ══════════════════════════════════════════════════════════════════════════════
#  CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

const TILE_SIZE:       float = 0.600
const NPC_LANE_OFFSET: float = TILE_SIZE * 0.22
const GRID_W:          int   = 16
const GRID_H:          int   = 16

const MAPS_DIR:        String = "user://maps"
const ACTIVE_MAP_PATH: String = "user://custom_map.json"

enum TileType { EMPTY = 0, STRAIGHT = 1, CURVE = 2, CROSS = 3, CROSS3 = 4 }

const OBJ_DUCK:    String = "duck"
const OBJ_STOP:    String = "stop_sign"
const OBJ_PARKING: String = "parking_sign"
const OBJ_NPC_CAR: String = "npc_car"


# ══════════════════════════════════════════════════════════════════════════════
#  STATE VARIABLES
# ══════════════════════════════════════════════════════════════════════════════

# ── Selection ─────────────────────────────────────────────────────────────────
var _selected_item: String = "straight"
var _selected_rot:  int    = 0

# ── Road tile layer  (cell grid) ──────────────────────────────────────────────
var _grid:       Dictionary = {}   # Vector2i → {type, rot}
var _tile_nodes: Dictionary = {}   # Vector2i → Node3D

# ── Duck layer  (cell grid) ───────────────────────────────────────────────────
var _duck_cells: Dictionary = {}   # Vector2i → {rot}
var _duck_nodes: Dictionary = {}   # Vector2i → Node3D

# ── Sign layer  (corner grid) ─────────────────────────────────────────────────
var _sign_corners:         Dictionary = {}   # Vector2i → {type, rot}
var _sign_nodes:           Dictionary = {}   # Vector2i → Node3D  (3-D model)
var _sign_indicator_nodes: Dictionary = {}   # Vector2i → Node3D  (flat overlay)

# ── Start point  (cell grid) ──────────────────────────────────────────────────
var _start_cell: Vector2i = Vector2i(-1, -1)
var _start_rot:  int      = 0
var _start_node: Node3D   = null

# ── NPC Car  (cell grid) ──────────────────────────────────────────────────────
var _npc_cell: Vector2i = Vector2i(-1, -1)
var _npc_rot:  int      = 0
var _npc_node: Node3D   = null

# ── NPC Path  (world-space waypoints) ─────────────────────────────────────────
var _npc_path_points:   Array = []   # Array[Vector3]
var _npc_path_nodes:    Array = []   # visual indicator Node3Ds
var _npc_path_variant:  int   = 0    # 0–11
var _npc_segment_sizes: Array = []   # points added per click, for undo
var _path_ghost_nodes:  Array = []   # hover-preview nodes

# ── 3-D scene refs ────────────────────────────────────────────────────────────
var _camera:                Camera3D
var _cell_hover:            MeshInstance3D
var _corner_hover:          MeshInstance3D
var _ghost_node:            Node3D
var _sign_hover_indicator:  Node3D
var _tiles_root:      Node3D
var _props_root:      Node3D
var _indicators_root: Node3D
var _tile_scenes:     Dictionary = {}
var _obj_scenes:      Dictionary = {}

# ── UI refs ───────────────────────────────────────────────────────────────────
var _name_edit:         LineEdit
var _map_dropdown:      OptionButton
var _sel_label:         Label
var _rot_label:         Label
var _start_coord_label: Label
var _status_label:      Label
var _npc_status_label:  Label
var _variant_buttons:   Array      = []
var _item_buttons:      Dictionary = {}
var _ui_panel:          PanelContainer

# ── Camera navigation state ───────────────────────────────────────────────────
var _mmb_dragging:   bool     = false
var _lmb_dragging:   bool     = false
var _lmb_last_cell:  Vector2i = Vector2i(-2, -2)
var _drag_dir:         Vector2i = Vector2i.ZERO
var _drag_cross3_cell: Vector2i = Vector2i(-2, -2)
var _rmb_dragging:     bool     = false
var _rmb_last_cell:    Vector2i = Vector2i(-2, -2)

const ZOOM_MIN: float  = 1.5
const ZOOM_MAX: float  = 30.0
const ZOOM_STEP: float = 0.12


# ══════════════════════════════════════════════════════════════════════════════
#  INITIALISATION
# ══════════════════════════════════════════════════════════════════════════════

func _ready() -> void:
	_ensure_maps_dir()
	_load_tile_scenes()
	_load_obj_scenes()
	_build_3d_scene()
	_build_ui()
	_refresh_map_list()
	_select_item("straight")
	print("[MapMaker] Ready")


func _ensure_maps_dir() -> void:
	var abs := ProjectSettings.globalize_path(MAPS_DIR)
	if not DirAccess.dir_exists_absolute(abs):
		DirAccess.make_dir_recursive_absolute(abs)


func _load_tile_scenes() -> void:
	var paths := {
		TileType.STRAIGHT: "res://scenes/tiles/tile_straight.tscn",
		TileType.CURVE:    "res://scenes/tiles/tile_curve.tscn",
		TileType.CROSS:    "res://scenes/tiles/tile_cross.tscn",
		TileType.CROSS3:   "res://scenes/tiles/tile_cross3.tscn",
	}
	for t in paths:
		_tile_scenes[t] = load(paths[t])
		if _tile_scenes[t] == null:
			push_warning("[MapMaker] Missing tile scene: " + paths[t])


func _load_obj_scenes() -> void:
	var paths := {
		OBJ_DUCK:    "res://scenes/objects/DuckieRagdool.tscn",
		OBJ_STOP:    "res://scenes/objects/obj_stop_sign.tscn",
		OBJ_PARKING: "res://scenes/objects/obj_parking_sign.tscn",
		OBJ_NPC_CAR: "res://scenes/robot/duckie_bot_npc.tscn",
	}
	for t in paths:
		_obj_scenes[t] = load(paths[t])
		if _obj_scenes[t] == null:
			push_warning("[MapMaker] Missing object scene: " + paths[t])


# ══════════════════════════════════════════════════════════════════════════════
#  3-D SCENE SETUP
# ══════════════════════════════════════════════════════════════════════════════

func _build_3d_scene() -> void:
	_add_camera()
	_add_environment()
	_add_ground_plane()
	_add_grid_lines()
	_add_hover_highlights()
	_tiles_root      = _add_named_node3d("Tiles")
	_props_root      = _add_named_node3d("Props")
	_indicators_root = _add_named_node3d("Indicators")


func _add_camera() -> void:
	_camera            = Camera3D.new()
	_camera.projection = Camera3D.PROJECTION_ORTHOGONAL
	_camera.size       = GRID_H * TILE_SIZE * 1.25
	_camera.near       = 0.1
	_camera.far        = 100.0
	_camera.position         = Vector3(GRID_W * TILE_SIZE * 0.5, 20.0, GRID_H * TILE_SIZE * 0.5)
	_camera.rotation_degrees = Vector3(-90.0, 0.0, 0.0)
	add_child(_camera)
	_camera.current = true


func _add_environment() -> void:
	var ew  := WorldEnvironment.new()
	var env := Environment.new()
	env.background_mode      = Environment.BG_COLOR
	env.background_color     = Color(0.18, 0.18, 0.18)
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color  = Color(1.0, 1.0, 1.0)
	env.ambient_light_energy = 1.8
	ew.environment = env
	add_child(ew)


func _add_ground_plane() -> void:
	var body := StaticBody3D.new()
	var col  := CollisionShape3D.new()
	col.shape = WorldBoundaryShape3D.new()
	body.add_child(col)
	add_child(body)

	var mi  := MeshInstance3D.new()
	var pm  := PlaneMesh.new()
	pm.size              = Vector2(GRID_W * TILE_SIZE * 3.0, GRID_H * TILE_SIZE * 3.0)
	mi.mesh              = pm
	mi.material_override = _flat_mat(Color(0.14, 0.22, 0.10))
	mi.position          = Vector3(GRID_W * TILE_SIZE * 0.5, 0.084, GRID_H * TILE_SIZE * 0.5)
	add_child(mi)


func _add_grid_lines() -> void:
	var imm := ImmediateMesh.new()
	var mi  := MeshInstance3D.new()
	mi.mesh              = imm
	mi.material_override = _flat_mat(Color(0.6, 0.6, 0.6, 0.7))
	add_child(mi)

	var y := 0.086
	imm.surface_begin(Mesh.PRIMITIVE_LINES)
	for c in range(GRID_W + 1):
		var x: float = c * TILE_SIZE
		imm.surface_add_vertex(Vector3(x, y, 0.0))
		imm.surface_add_vertex(Vector3(x, y, GRID_H * TILE_SIZE))
	for r in range(GRID_H + 1):
		var z: float = r * TILE_SIZE
		imm.surface_add_vertex(Vector3(0.0,                y, z))
		imm.surface_add_vertex(Vector3(GRID_W * TILE_SIZE, y, z))
	imm.surface_end()


func _add_hover_highlights() -> void:
	_cell_hover   = _make_hover_quad(TILE_SIZE * 0.92, Color(1.0, 1.0, 0.2, 0.30), 0.087)
	_corner_hover = _make_hover_quad(TILE_SIZE * 0.20, Color(0.2, 1.0, 0.35, 0.95), 0.089)


func _add_named_node3d(node_name: String) -> Node3D:
	var n := Node3D.new()
	n.name = node_name
	add_child(n)
	return n

func _make_hover_quad(side: float, color: Color, y_pos: float) -> MeshInstance3D:
	var mi  := MeshInstance3D.new()
	var pm  := PlaneMesh.new()
	pm.size              = Vector2(side, side)
	mi.mesh              = pm
	mi.material_override = _flat_mat(color)
	mi.position.y        = y_pos
	mi.visible           = false
	add_child(mi)
	return mi

func _flat_mat(color: Color) -> StandardMaterial3D:
	return IndicatorFactory.flat_mat(color)


# ══════════════════════════════════════════════════════════════════════════════
#  USER INTERFACE
# ══════════════════════════════════════════════════════════════════════════════

func _build_ui() -> void:
	var cl := CanvasLayer.new()
	add_child(cl)
	var panel := PanelContainer.new()
	panel.position = Vector2(10.0, 10.0)
	cl.add_child(panel)
	_ui_panel = panel
	var scroll := ScrollContainer.new()
	scroll.custom_minimum_size    = Vector2(268.0, get_viewport().get_visible_rect().size.y - 30.0)
	scroll.vertical_scroll_mode   = ScrollContainer.SCROLL_MODE_AUTO
	scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	panel.add_child(scroll)
	var vb := VBoxContainer.new()
	vb.custom_minimum_size   = Vector2(248.0, 0.0)
	vb.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(vb)

	_add_label(vb, "─── MAP MAKER ───")
	_add_sep(vb)
	_build_ui_map_io(vb)
	_build_ui_tools(vb)
	_build_ui_npc(vb)
	_build_ui_controls(vb)
	_build_ui_status(vb)


func _build_ui_map_io(vb: VBoxContainer) -> void:
	_add_label(vb, "MAP NAME")
	_name_edit = LineEdit.new()
	_name_edit.text             = "my_map"
	_name_edit.placeholder_text = "enter map name..."
	vb.add_child(_name_edit)
	_add_button(vb, "Save Map",                 _save_map)
	_add_button(vb, "⬇ Export as .tscn Scene", _export_as_scene)
	_add_sep(vb)

	_add_label(vb, "SAVED MAPS")
	_map_dropdown = OptionButton.new()
	_map_dropdown.custom_minimum_size = Vector2(230, 0)
	vb.add_child(_map_dropdown)
	var hb := HBoxContainer.new()
	vb.add_child(hb)
	_hb_button(hb, "Load", _load_selected_map)
	_hb_button(hb, "↻",   _refresh_map_list)
	_hb_button(hb, "Del",  _delete_selected_map)
	_add_sep(vb)


func _build_ui_tools(vb: VBoxContainer) -> void:
	_add_label(vb, "ROAD TILES")
	_add_swatch_toggle(vb, "straight", "Straight",       Color(0.40, 0.40, 0.40))
	_add_swatch_toggle(vb, "curve",    "Curve",          Color(0.50, 0.45, 0.35))
	_add_swatch_toggle(vb, "cross3",   "3-Way Crossing", Color(0.35, 0.30, 0.30))
	_add_swatch_toggle(vb, "cross",    "4-Way Crossing", Color(0.30, 0.30, 0.30))
	_add_sep(vb)
	_add_label(vb, "PROPS")
	_add_swatch_toggle(vb, OBJ_DUCK,    "Duck  (on road, cell center)",   Color(0.95, 0.80, 0.10))
	_add_swatch_toggle(vb, OBJ_STOP,    "Stop Sign  (road edge, corner)", Color(0.90, 0.08, 0.08))
	_add_swatch_toggle(vb, OBJ_PARKING, "Parking Sign  (road edge)",      Color(0.08, 0.20, 0.85))
	_add_sep(vb)
	_add_swatch_toggle(vb, "start", "Set Start Point", Color(1.0, 0.82, 0.0))
	_add_swatch_toggle(vb, "",      "Erase",           Color(0.7, 0.15, 0.15))
	_add_sep(vb)


func _build_ui_npc(vb: VBoxContainer) -> void:
	_add_label(vb, "NPC CAR")
	_add_swatch_toggle(vb, OBJ_NPC_CAR, "NPC Car  (place on road)",          Color(1.0, 0.45, 0.10))
	_add_swatch_toggle(vb, "npc_path",  "NPC Path  (click tile to add pts)", Color(0.75, 0.10, 0.90))

	# 3 rows × 4 columns: straight (blue) / right turn (orange) / left turn (green)
	var labels := ["S↑N","S↓S","S→E","S←W","R:N→E","R:E→S","R:S→W","R:W→N","L:N→W","L:E→N","L:S→E","L:W→S"]
	var colors := [
		Color(0.3,0.7,1.0), Color(0.3,0.7,1.0), Color(0.3,0.7,1.0), Color(0.3,0.7,1.0),
		Color(1.0,0.6,0.2), Color(1.0,0.6,0.2), Color(1.0,0.6,0.2), Color(1.0,0.6,0.2),
		Color(0.5,1.0,0.4), Color(0.5,1.0,0.4), Color(0.5,1.0,0.4), Color(0.5,1.0,0.4),
	]
	for row in range(3):
		var hb := HBoxContainer.new()
		vb.add_child(hb)
		for col in range(4):
			var vi   := row * 4 + col
			var vbtn := Button.new()
			vbtn.text                   = labels[vi]
			vbtn.toggle_mode            = true
			vbtn.button_pressed         = (vi == 0)
			vbtn.size_flags_horizontal  = Control.SIZE_EXPAND_FILL
			vbtn.add_theme_color_override("font_color", colors[vi])
			var captured := vi
			vbtn.pressed.connect(func(): _select_variant(captured))
			_variant_buttons.append(vbtn)
			hb.add_child(vbtn)

	var hb_npc := HBoxContainer.new()
	vb.add_child(hb_npc)
	_hb_button(hb_npc, "↩ Undo",  _undo_npc_waypoint)
	_hb_button(hb_npc, "✕ Clear", _clear_npc_path)
	_npc_status_label = _add_label(vb, "NPC: no car · 0 points")
	_add_sep(vb)


func _build_ui_controls(vb: VBoxContainer) -> void:
	_sel_label = _add_label(vb, "Selected: Straight")
	_rot_label = _add_label(vb, "Rotation: 0°")
	_add_button(vb, "Rotate 90°", _rotate_cw)
	_add_sep(vb)
	var hb := HBoxContainer.new()
	vb.add_child(hb)
	_hb_button(hb, "Clear Roads", _clear_roads)
	_hb_button(hb, "Clear Props", _clear_props)
	_add_sep(vb)


func _build_ui_status(vb: VBoxContainer) -> void:
	_start_coord_label = _add_label(vb, "Start: not set")
	_status_label      = _add_label(vb, "Roads: 0  Ducks: 0  Signs: 0")
	_add_sep(vb)
	_add_label(vb, "LClick/Drag: place\nRClick: erase\nMMB: pan  Scroll: zoom\nR: rotate")


# ── UI widget helpers ─────────────────────────────────────────────────────────

func _add_label(parent: Control, text: String) -> Label:
	var l := Label.new()
	l.text = text
	parent.add_child(l)
	return l

func _add_sep(parent: Control) -> void:
	parent.add_child(HSeparator.new())

func _add_button(parent: Control, text: String, cb: Callable) -> Button:
	var b := Button.new()
	b.text = text
	b.pressed.connect(cb)
	parent.add_child(b)
	return b

func _hb_button(hb: HBoxContainer, text: String, cb: Callable) -> void:
	var b := Button.new()
	b.text                  = text
	b.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	b.pressed.connect(cb)
	hb.add_child(b)

func _add_swatch_toggle(parent: Control, key: String, label_text: String, color: Color) -> void:
	var hb := HBoxContainer.new()
	parent.add_child(hb)
	var sw := ColorRect.new()
	sw.custom_minimum_size = Vector2(14, 20)
	sw.color = color
	hb.add_child(sw)
	var b := Button.new()
	b.text                  = label_text
	b.toggle_mode           = true
	b.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	b.pressed.connect(func(): _select_item(key))
	_item_buttons[key] = b
	hb.add_child(b)


# ══════════════════════════════════════════════════════════════════════════════
#  SELECTION & GHOST
# ══════════════════════════════════════════════════════════════════════════════

func _select_item(key: String) -> void:
	_selected_item = key
	for k in _item_buttons:
		_item_buttons[k].button_pressed = (k == key)
	_sel_label.text = "Selected: " + _item_display(key)
	_rebuild_ghost()


func _select_variant(vi: int) -> void:
	_npc_path_variant = vi
	for i in range(_variant_buttons.size()):
		_variant_buttons[i].button_pressed = (i == vi)


func _rebuild_ghost() -> void:
	if is_instance_valid(_ghost_node):
		_ghost_node.queue_free()
	_ghost_node = null
	if is_instance_valid(_sign_hover_indicator):
		_sign_hover_indicator.queue_free()
	_sign_hover_indicator = null

	var scene: PackedScene = null
	match _selected_item:
		"straight":           scene = _tile_scenes.get(TileType.STRAIGHT)
		"curve":              scene = _tile_scenes.get(TileType.CURVE)
		"cross3":             scene = _tile_scenes.get(TileType.CROSS3)
		"cross":              scene = _tile_scenes.get(TileType.CROSS)
		OBJ_DUCK:             scene = _obj_scenes.get(OBJ_DUCK)
		OBJ_STOP:             scene = _obj_scenes.get(OBJ_STOP)
		OBJ_PARKING:          scene = _obj_scenes.get(OBJ_PARKING)
		OBJ_NPC_CAR, "start": scene = load("res://models/VehicleCorected.glb") as PackedScene

	if scene != null:
		_ghost_node = scene.instantiate() as Node3D
		if _ghost_node is RigidBody3D:
			(_ghost_node as RigidBody3D).freeze = true
		var rot_offset := 180 if _selected_item in [OBJ_NPC_CAR, "start"] else 0
		_ghost_node.rotation_degrees.y = _selected_rot + rot_offset
		_ghost_node.visible = false
		IndicatorFactory.apply_ghost_material(_ghost_node)
		add_child(_ghost_node)

	if _is_sign_item(_selected_item):
		_sign_hover_indicator = IndicatorFactory.make_sign_indicator(
			Vector3.ZERO, _selected_item, _selected_rot
		)
		_sign_hover_indicator.visible = false
		add_child(_sign_hover_indicator)


func _item_display(key: String) -> String:
	match key:
		"straight":  return "Straight"
		"curve":     return "Curve"
		"cross3":    return "3-Way Crossing"
		"cross":     return "4-Way Crossing"
		OBJ_DUCK:    return "Duck"
		OBJ_STOP:    return "Stop Sign"
		OBJ_PARKING: return "Parking Sign"
		"start":     return "Start Point"
		OBJ_NPC_CAR: return "NPC Car"
		"npc_path":  return "NPC Waypoint"
		"":          return "Erase"
	return key

func _is_sign_item(item: String) -> bool:
	return item in [OBJ_STOP, OBJ_PARKING]


# ══════════════════════════════════════════════════════════════════════════════
#  INPUT HANDLING
# ══════════════════════════════════════════════════════════════════════════════

func _mouse_over_ui() -> bool:
	if _ui_panel == null:
		return false
	var mp := get_viewport().get_mouse_position()
	var r  := _ui_panel.get_global_rect()
	return r.has_point(mp)


func _unhandled_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo:
		if event.keycode == KEY_R:
			if _selected_item == "npc_path":
				_select_variant((_npc_path_variant + 1) % _variant_buttons.size())
			else:
				_rotate_cw()
			return

	if _mouse_over_ui():
		return

	if event is InputEventMagnifyGesture:
		_camera.size = clamp(_camera.size / event.factor, ZOOM_MIN, ZOOM_MAX)
		return

	if event is InputEventMouseButton:
		match event.button_index:
			MOUSE_BUTTON_WHEEL_UP:
				_camera.size = clamp(_camera.size * (1.0 - ZOOM_STEP), ZOOM_MIN, ZOOM_MAX)
			MOUSE_BUTTON_WHEEL_DOWN:
				_camera.size = clamp(_camera.size * (1.0 + ZOOM_STEP), ZOOM_MIN, ZOOM_MAX)
			MOUSE_BUTTON_MIDDLE:
				_mmb_dragging = event.pressed
			MOUSE_BUTTON_LEFT:
				if event.pressed:
					_lmb_dragging      = true
					_drag_dir          = Vector2i.ZERO
					_drag_cross3_cell  = Vector2i(-2, -2)
					_lmb_last_cell     = _mouse_to_cell()
					_handle_mouse_click(MOUSE_BUTTON_LEFT)
				else:
					_lmb_dragging = false
			MOUSE_BUTTON_RIGHT:
				if event.pressed:
					_rmb_dragging  = true
					_rmb_last_cell = _mouse_to_cell()
					_handle_mouse_click(MOUSE_BUTTON_RIGHT)
				else:
					_rmb_dragging = false
			_:
				if event.pressed:
					_handle_mouse_click(event.button_index)
		return

	if event is InputEventMouseMotion:
		if _mmb_dragging:
			_pan_camera(event.relative)
		if _lmb_dragging:
			var drag_cell := _mouse_to_cell()
			if _cell_valid(drag_cell) and drag_cell != _lmb_last_cell:
				_handle_lmb_drag_paint(drag_cell)
				_lmb_last_cell = drag_cell
		if _rmb_dragging:
			var drag_cell := _mouse_to_cell()
			if _cell_valid(drag_cell) and drag_cell != _rmb_last_cell:
				_rmb_last_cell = drag_cell
				_handle_rmb_drag_erase(drag_cell)
		_handle_mouse_move()
		return


func _handle_mouse_move() -> void:
	if _is_sign_item(_selected_item):
		_clear_path_ghost()
		_cell_hover.visible = false
		var corner := _mouse_to_corner()
		var valid  := _corner_valid(corner)
		_corner_hover.visible = valid
		if valid:
			var wp := _corner_world_pos(corner)
			_corner_hover.position = Vector3(wp.x, 0.009, wp.z)
			if is_instance_valid(_ghost_node):
				_ghost_node.position = Vector3(wp.x, 0.091, wp.z)
				_ghost_node.visible  = true
			if is_instance_valid(_sign_hover_indicator):
				_sign_hover_indicator.position = Vector3(wp.x, 0.086, wp.z)
				_sign_hover_indicator.visible  = true
		else:
			if is_instance_valid(_ghost_node):
				_ghost_node.visible = false
			if is_instance_valid(_sign_hover_indicator):
				_sign_hover_indicator.visible = false
	else:
		_corner_hover.visible = false
		var cell  := _mouse_to_cell()
		var valid := _cell_valid(cell)
		_cell_hover.visible = valid
		if valid:
			var wp := _cell_world_center(cell)
			_cell_hover.position   = wp
			_cell_hover.position.y = 0.087
			if _selected_item == "npc_path":
				_show_path_ghost(cell)
				if is_instance_valid(_ghost_node):
					_ghost_node.visible = false
			elif is_instance_valid(_ghost_node):
				_clear_path_ghost()
				if _selected_item in [OBJ_NPC_CAR, "start"]:
					_ghost_node.position = _npc_world_pos(cell, _selected_rot)
				elif _selected_item == OBJ_DUCK:
					_ghost_node.position = _duck_world_pos(cell, _selected_rot)
				else:
					_ghost_node.position = Vector3(wp.x, 0.091, wp.z)
				_ghost_node.visible = true
		else:
			_clear_path_ghost()
			if is_instance_valid(_ghost_node):
				_ghost_node.visible = false


func _handle_mouse_click(button: int) -> void:
	if _is_sign_item(_selected_item):
		var corner := _mouse_to_corner()
		if not _corner_valid(corner):
			return
		if button == MOUSE_BUTTON_LEFT:
			print("[MapMaker] Sign click: item=%s rot=%d corner=%s" % [_selected_item, _selected_rot, str(corner)])
			_place_sign(corner, _selected_item, _selected_rot)
		elif button == MOUSE_BUTTON_RIGHT:
			_erase_sign(corner)
	else:
		var cell := _mouse_to_cell()
		if not _cell_valid(cell):
			return
		if button == MOUSE_BUTTON_LEFT:
			_handle_cell_left(cell)
		elif button == MOUSE_BUTTON_RIGHT:
			_handle_cell_right(cell)


func _handle_cell_left(cell: Vector2i) -> void:
	match _selected_item:
		"straight":  _place_tile(cell, TileType.STRAIGHT, _selected_rot)
		"curve":     _place_tile(cell, TileType.CURVE,    _selected_rot)
		"cross3":    _place_tile(cell, TileType.CROSS3,   _selected_rot)
		"cross":     _place_tile(cell, TileType.CROSS,    _selected_rot)
		OBJ_DUCK:    _place_duck(cell, _selected_rot)
		"start":     _set_start(cell)
		OBJ_NPC_CAR: _set_npc(cell, _selected_rot)
		"npc_path":  _add_npc_waypoint(cell)
		"":
			_erase_tile(cell)
			_erase_duck(cell)
			_maybe_erase_start(cell)


func _handle_cell_right(cell: Vector2i) -> void:
	match _selected_item:
		"straight", "curve", "cross3", "cross": _erase_tile(cell)
		OBJ_DUCK:    _erase_duck(cell)
		"start":     _maybe_erase_start(cell)
		OBJ_NPC_CAR: _maybe_erase_npc(cell)
		"":
			_erase_tile(cell)
			_erase_duck(cell)
			_maybe_erase_start(cell)


func _rotate_cw() -> void:
	_selected_rot = (_selected_rot + 90) % 360
	_rot_label.text = "Rotation: %d°" % _selected_rot
	if is_instance_valid(_ghost_node):
		var rot_offset := 180 if _selected_item in [OBJ_NPC_CAR, "start"] else 0
		_ghost_node.rotation_degrees.y = _selected_rot + rot_offset
		if _selected_item in [OBJ_NPC_CAR, "start"]:
			var cell := _mouse_to_cell()
			if _cell_valid(cell):
				_ghost_node.position = _npc_world_pos(cell, _selected_rot)
		elif _selected_item == OBJ_DUCK:
			var cell := _mouse_to_cell()
			if _cell_valid(cell):
				_ghost_node.position = _duck_world_pos(cell, _selected_rot)
	if is_instance_valid(_sign_hover_indicator):
		_sign_hover_indicator.rotation_degrees.y = _selected_rot


func _pan_camera(screen_delta: Vector2) -> void:
	var vp_h := get_viewport().get_visible_rect().size.y
	var wpp  := _camera.size / vp_h
	_camera.position.x -= screen_delta.x * wpp
	_camera.position.z -= screen_delta.y * wpp


func _handle_lmb_drag_paint(cell: Vector2i) -> void:
	if not _cell_valid(cell):
		return

	# Upgrade tentative CROSS3 → CROSS4 now that drag is continuing past it
	if _drag_cross3_cell == _lmb_last_cell and _cell_valid(_drag_cross3_cell):
		_place_tile(_drag_cross3_cell, TileType.CROSS, 0)
	_drag_cross3_cell = Vector2i(-2, -2)

	match _selected_item:
		"curve":  _place_tile(cell, TileType.CURVE,  _selected_rot); return
		"cross3": _place_tile(cell, TileType.CROSS3, _selected_rot); return
		"cross":  _place_tile(cell, TileType.CROSS,  _selected_rot); return
		"":
			_erase_tile(cell)
			_erase_duck(cell)
			_drag_dir = Vector2i.ZERO
			return

	# Smart straight-drag logic
	var delta   := cell - _lmb_last_cell
	var new_dir := _dir_from_delta(delta)
	if new_dir == Vector2i.ZERO:
		return

	# On first drag step, reorient the initial tile — plain overwrite, no cross detection
	if _drag_dir == Vector2i.ZERO and _cell_valid(_lmb_last_cell):
		_place_tile(_lmb_last_cell, TileType.STRAIGHT, 90 if new_dir.x != 0 else 0)

	# Fill intermediate cells when mouse jumped multiple cells
	var steps: int = max(abs(delta.x), abs(delta.y))
	for i in range(1, steps):
		var mc: Vector2i = _lmb_last_cell + Vector2i(
			int(round(float(delta.x) * i / steps)),
			int(round(float(delta.y) * i / steps))
		)
		if _cell_valid(mc) and mc != cell:
			_place_smart_straight(mc, new_dir)

	if _drag_dir != Vector2i.ZERO and new_dir != _drag_dir:
		_place_tile(_lmb_last_cell, TileType.CURVE, _curve_rot_for_turn(_drag_dir, new_dir))
		_place_smart_straight(cell, new_dir)
	else:
		_place_smart_straight(cell, new_dir)

	_drag_dir = new_dir


func _handle_rmb_drag_erase(cell: Vector2i) -> void:
	if not _cell_valid(cell):
		return
	if _is_sign_item(_selected_item):
		var corner := _mouse_to_corner()
		if _corner_valid(corner):
			_erase_sign(corner)
	else:
		_erase_tile(cell)
		_erase_duck(cell)


func _place_smart_straight(cell: Vector2i, dir: Vector2i) -> void:
	var new_horiz: bool = dir.x != 0
	if _grid.has(cell):
		var ex: Dictionary = _grid[cell]
		var ex_type: int   = ex["type"]
		match ex_type:
			TileType.STRAIGHT:
				var ex_horiz: bool = (ex["rot"] % 180 == 90)
				if ex_horiz != new_horiz:
					# Perpendicular crossing: tentative T — upgrade to 4-way if drag continues
					_place_tile(cell, TileType.CROSS3, _cross3_rot_for_closed(dir))
					_drag_cross3_cell = cell
				return
			TileType.CURVE:
				var closed: Vector2i = _cross3_closed_face_for_curve_and_dir(ex["rot"], dir)
				_place_tile(cell, TileType.CROSS3, _cross3_rot_for_closed(closed))
				_drag_cross3_cell = cell
				return
			TileType.CROSS3:
				var closed: Vector2i = _cross3_closed_face(ex["rot"])
				if closed == dir or closed == -dir:
					# Tentative: keep as CROSS3 for now, upgrade if drag continues
					_drag_cross3_cell = cell
				return
			TileType.CROSS:
				return
	_place_tile(cell, TileType.STRAIGHT, 90 if new_horiz else 0)


# Returns which face of CROSS3 should be closed when a new straight (dir) hits a curve.
# Curve opens two faces; new road opens a third; the fourth stays closed.
func _cross3_closed_face_for_curve_and_dir(curve_rot: int, dir: Vector2i) -> Vector2i:
	var curve_faces: Array = _curve_open_faces(curve_rot)
	var all: Array = [Vector2i(0,-1), Vector2i(0,1), Vector2i(1,0), Vector2i(-1,0)]
	for f: Vector2i in all:
		if f != dir and f != -dir and not (f in curve_faces):
			return f
	return Vector2i(0, 1)  # fallback


func _curve_open_faces(rot: int) -> Array:
	match rot:
		0:   return [Vector2i(0,-1), Vector2i(-1,0)]  # N+W
		90:  return [Vector2i(0, 1), Vector2i(-1,0)]  # S+W
		180: return [Vector2i(0, 1), Vector2i( 1,0)]  # S+E
		_:   return [Vector2i(0,-1), Vector2i( 1,0)]  # N+E (270)


# closed_face → CROSS3 rotation  (rot=0: closed S, 90: E, 180: N, 270: W)
func _cross3_rot_for_closed(closed: Vector2i) -> int:
	if closed == Vector2i(0,  1): return 0
	if closed == Vector2i(1,  0): return 90
	if closed == Vector2i(0, -1): return 180
	return 270


func _cross3_closed_face(rot: int) -> Vector2i:
	match rot:
		0:   return Vector2i(0,  1)
		90:  return Vector2i(1,  0)
		180: return Vector2i(0, -1)
		_:   return Vector2i(-1, 0)


func _dir_from_delta(delta: Vector2i) -> Vector2i:
	if delta == Vector2i.ZERO:
		return Vector2i.ZERO
	if abs(delta.x) >= abs(delta.y):
		return Vector2i(signi(delta.x), 0)
	return Vector2i(0, signi(delta.y))


func _curve_rot_for_turn(from_dir: Vector2i, to_dir: Vector2i) -> int:
	# entry = face we came through; exit = face we leave through
	var entry: Vector2i = -from_dir
	var ex:    Vector2i = to_dir
	var n: Vector2i = Vector2i(0, -1);  var s: Vector2i = Vector2i(0,  1)
	var e: Vector2i = Vector2i(1,  0);  var w: Vector2i = Vector2i(-1, 0)
	if (entry == n or ex == n) and (entry == w or ex == w): return 0
	if (entry == s or ex == s) and (entry == w or ex == w): return 90
	if (entry == s or ex == s) and (entry == e or ex == e): return 180
	return 270


# ══════════════════════════════════════════════════════════════════════════════
#  GRID MATH
# ══════════════════════════════════════════════════════════════════════════════

func _mouse_to_cell() -> Vector2i:
	var mp     := get_viewport().get_mouse_position()
	var origin := _camera.project_ray_origin(mp)
	var dir    := _camera.project_ray_normal(mp)
	if abs(dir.y) < 0.0001:
		return Vector2i(-1, -1)
	var t  := -origin.y / dir.y
	var wp := origin + dir * t
	return Vector2i(int(floor(wp.x / TILE_SIZE)), int(floor(wp.z / TILE_SIZE)))


func _mouse_to_corner() -> Vector2i:
	var mp     := get_viewport().get_mouse_position()
	var origin := _camera.project_ray_origin(mp)
	var dir    := _camera.project_ray_normal(mp)
	if abs(dir.y) < 0.0001:
		return Vector2i(-1, -1)
	var t  := -origin.y / dir.y
	var wp := origin + dir * t
	return Vector2i(int(round(wp.x / TILE_SIZE)), int(round(wp.z / TILE_SIZE)))


func _cell_valid(c: Vector2i) -> bool:
	return c.x >= 0 and c.x < GRID_W and c.y >= 0 and c.y < GRID_H

func _corner_valid(c: Vector2i) -> bool:
	return c.x >= 0 and c.x <= GRID_W and c.y >= 0 and c.y <= GRID_H

func _cell_world_center(c: Vector2i) -> Vector3:
	return Vector3((c.x + 0.5) * TILE_SIZE, 0.0, (c.y + 0.5) * TILE_SIZE)

func _corner_world_pos(c: Vector2i) -> Vector3:
	return Vector3(c.x * TILE_SIZE, 0.0, c.y * TILE_SIZE)

func _npc_world_pos(cell: Vector2i, rot_deg: int) -> Vector3:
	var center := _cell_world_center(cell)
	var a      := deg_to_rad(float(rot_deg))
	return Vector3(
		center.x + NPC_LANE_OFFSET * cos(a),
		0.155,
		center.z - NPC_LANE_OFFSET * sin(a)
	)

func _duck_world_pos(cell: Vector2i, rot_deg: int) -> Vector3:
	var center := _cell_world_center(cell)
	var a      := deg_to_rad(float(rot_deg))
	return Vector3(
		center.x + NPC_LANE_OFFSET * cos(a),
		0.130,   # road surface 0.091 + sphere radius 0.05 - sphere local offset 0.012
		center.z - NPC_LANE_OFFSET * sin(a)
	)


# ══════════════════════════════════════════════════════════════════════════════
#  ROAD TILES
# ══════════════════════════════════════════════════════════════════════════════

func _place_tile(cell: Vector2i, type: int, rot: int) -> void:
	_erase_tile(cell)
	var scene: PackedScene = _tile_scenes.get(type)
	if scene == null:
		return
	var node: Node3D = scene.instantiate()
	var twp          := _cell_world_center(cell)
	node.position           = Vector3(twp.x, 0.091, twp.z)
	node.rotation_degrees.y = rot
	_tiles_root.add_child(node)
	_tile_nodes[cell] = node
	_grid[cell]       = {"type": type, "rot": rot}
	_refresh_status()

func _erase_tile(cell: Vector2i) -> void:
	if _tile_nodes.has(cell):
		_tile_nodes[cell].queue_free()
		_tile_nodes.erase(cell)
	_grid.erase(cell)
	_refresh_status()


# ══════════════════════════════════════════════════════════════════════════════
#  DUCKS
# ══════════════════════════════════════════════════════════════════════════════

func _place_duck(cell: Vector2i, rot: int) -> void:
	_erase_duck(cell)
	var scene: PackedScene = _obj_scenes.get(OBJ_DUCK)
	if scene == null:
		return
	var node: Node3D = scene.instantiate()
	if node is RigidBody3D:
		(node as RigidBody3D).freeze = true
	node.position           = _duck_world_pos(cell, rot)
	node.rotation_degrees.y = rot
	_props_root.add_child(node)
	_duck_nodes[cell] = node
	_duck_cells[cell] = {"rot": rot}
	_refresh_status()

func _erase_duck(cell: Vector2i) -> void:
	if _duck_nodes.has(cell):
		_duck_nodes[cell].queue_free()
		_duck_nodes.erase(cell)
	_duck_cells.erase(cell)
	_refresh_status()


# ══════════════════════════════════════════════════════════════════════════════
#  SIGNS
# ══════════════════════════════════════════════════════════════════════════════

func _place_sign(corner: Vector2i, type: String, rot: int) -> void:
	_erase_sign(corner)
	var scene: PackedScene = _obj_scenes.get(type)
	if scene != null:
		var node: Node3D = scene.instantiate()
		var swp          := _corner_world_pos(corner)
		node.position           = Vector3(swp.x, 0.091, swp.z)
		node.rotation_degrees.y = rot
		_props_root.add_child(node)
		_sign_nodes[corner] = node
	_sign_corners[corner] = {"type": type, "rot": rot}
	var icwp := _corner_world_pos(corner)
	var ind  := IndicatorFactory.make_sign_indicator(Vector3(icwp.x, 0.086, icwp.z), type, rot)
	_indicators_root.add_child(ind)
	_sign_indicator_nodes[corner] = ind
	_refresh_status()

func _erase_sign(corner: Vector2i) -> void:
	if _sign_nodes.has(corner):
		_sign_nodes[corner].queue_free()
		_sign_nodes.erase(corner)
	if _sign_indicator_nodes.has(corner):
		_sign_indicator_nodes[corner].queue_free()
		_sign_indicator_nodes.erase(corner)
	_sign_corners.erase(corner)
	_refresh_status()


# ══════════════════════════════════════════════════════════════════════════════
#  START POINT
# ══════════════════════════════════════════════════════════════════════════════

func _set_start(cell: Vector2i) -> void:
	if _start_cell == cell:
		_clear_start_visual()
		_start_cell = Vector2i(-1, -1)
		_refresh_status()
		return
	_clear_start_visual()
	_start_cell = cell
	_start_rot  = _selected_rot
	var bot_scene := load("res://models/VehicleCorected.glb") as PackedScene
	if bot_scene != null:
		var bot := bot_scene.instantiate() as Node3D
		bot.name               = "DuckieBot"
		bot.position           = _npc_world_pos(cell, _selected_rot)
		bot.rotation_degrees.y = _selected_rot + 180
		add_child(bot)
		_start_node = bot
	else:
		var wp  := _cell_world_center(cell)
		var mi  := MeshInstance3D.new()
		var pm  := PlaneMesh.new()
		pm.size              = Vector2(TILE_SIZE * 0.72, TILE_SIZE * 0.72)
		mi.mesh              = pm
		mi.material_override = _flat_mat(Color(1.0, 0.82, 0.0, 0.92))
		mi.rotation_degrees.y = 45.0
		mi.position           = Vector3(wp.x, 0.009, wp.z)
		add_child(mi)
		_start_node = mi
	_refresh_status()

## Sets the start point with an explicit rotation (used during map loading).
func _set_start_at(cell: Vector2i, rot: int) -> void:
	var prev      := _selected_rot
	_selected_rot  = rot
	_set_start(cell)
	_selected_rot  = prev

func _maybe_erase_start(cell: Vector2i) -> void:
	if _start_cell == cell:
		_clear_start_visual()
		_start_cell = Vector2i(-1, -1)
		_refresh_status()

func _clear_start_visual() -> void:
	if _start_node != null:
		_start_node.queue_free()
		_start_node = null


# ══════════════════════════════════════════════════════════════════════════════
#  NPC CAR
# ══════════════════════════════════════════════════════════════════════════════

func _set_npc(cell: Vector2i, rot: int) -> void:
	_clear_npc_visual()
	_npc_cell = cell
	_npc_rot  = rot
	var scene := load("res://models/VehicleCorected.glb") as PackedScene
	if scene != null:
		var node := scene.instantiate() as Node3D
		node.position           = _npc_world_pos(cell, rot)
		node.rotation_degrees.y = rot + 180
		add_child(node)
		_npc_node = node
	_refresh_npc_status()

func _maybe_erase_npc(cell: Vector2i) -> void:
	if _npc_cell == cell:
		_clear_npc_visual()
		_npc_cell = Vector2i(-1, -1)
		_refresh_npc_status()

func _clear_npc_visual() -> void:
	if _npc_node != null:
		_npc_node.queue_free()
		_npc_node = null


# ══════════════════════════════════════════════════════════════════════════════
#  NPC PATH WAYPOINTS
# ══════════════════════════════════════════════════════════════════════════════

func _add_npc_waypoint(cell: Vector2i) -> void:
	if _npc_path_points.size() == 0 and _npc_cell.x >= 0:
		_npc_path_points.append(_npc_world_pos(_npc_cell, _npc_rot))
		_npc_segment_sizes.append(1)
	var pts := _variant_points(cell)
	for p in pts:
		_npc_path_points.append(p)
	_npc_segment_sizes.append(pts.size())
	_clear_path_ghost()
	_rebuild_path_visuals()
	_refresh_npc_status()


func _undo_npc_waypoint() -> void:
	if _npc_segment_sizes.is_empty():
		return
	var remove := int(_npc_segment_sizes[-1])
	_npc_segment_sizes.resize(_npc_segment_sizes.size() - 1)
	_npc_path_points.resize(_npc_path_points.size() - remove)
	if _npc_path_points.size() == 1 and _npc_segment_sizes.size() == 1 \
			and int(_npc_segment_sizes[0]) == 1:
		_npc_path_points.clear()
		_npc_segment_sizes.clear()
	_rebuild_path_visuals()
	_refresh_npc_status()


func _clear_npc_path() -> void:
	_npc_path_points.clear()
	_npc_segment_sizes.clear()
	for n in _npc_path_nodes:
		if is_instance_valid(n):
			n.queue_free()
	_npc_path_nodes.clear()
	_clear_path_ghost()
	_refresh_npc_status()


func _variant_points(cell: Vector2i) -> Array:
	return NpcPathVariants.get_points(
		_cell_world_center(cell), _npc_path_variant, TILE_SIZE, NPC_LANE_OFFSET
	)


func _rebuild_path_visuals() -> void:
	for n in _npc_path_nodes:
		if is_instance_valid(n):
			n.queue_free()
	_npc_path_nodes.clear()

	var count := _npc_path_points.size()
	for i in range(count):
		var wp: Vector3 = _npc_path_points[i]
		var col: Color
		if   i == 0:         col = Color(0.1, 0.9, 0.2)
		elif i == count - 1: col = Color(1.0, 0.45, 0.05)
		else:                col = Color(0.7, 0.1, 0.9)
		var disc := IndicatorFactory.make_waypoint_disc(
			Vector3(wp.x, 0.090, wp.z), col, i + 1, TILE_SIZE
		)
		_indicators_root.add_child(disc)
		_npc_path_nodes.append(disc)
		if i + 1 < count:
			var nwp: Vector3 = _npc_path_points[i + 1]
			var arrow := IndicatorFactory.make_path_arrow(
				Vector3(wp.x, 0.090, wp.z), Vector3(nwp.x, 0.090, nwp.z), TILE_SIZE
			)
			_indicators_root.add_child(arrow)
			_npc_path_nodes.append(arrow)


func _show_path_ghost(cell: Vector2i) -> void:
	_clear_path_ghost()
	var pts := _variant_points(cell)
	var ghost_colors := [
		Color(0.4, 1.0, 0.5, 0.7), Color(0.6, 0.7, 1.0, 0.7), Color(0.8, 0.5, 1.0, 0.7),
		Color(1.0, 0.6, 0.5, 0.7), Color(1.0, 0.7, 0.3, 0.7),
	]
	for i in range(pts.size()):
		var disc := IndicatorFactory.make_waypoint_disc(pts[i], ghost_colors[i], i + 1, TILE_SIZE)
		_indicators_root.add_child(disc)
		_path_ghost_nodes.append(disc)
	for i in range(pts.size() - 1):
		var arrow := IndicatorFactory.make_path_arrow(pts[i], pts[i + 1], TILE_SIZE)
		_indicators_root.add_child(arrow)
		_path_ghost_nodes.append(arrow)

func _clear_path_ghost() -> void:
	for n in _path_ghost_nodes:
		if is_instance_valid(n):
			n.queue_free()
	_path_ghost_nodes.clear()

func _refresh_npc_status() -> void:
	if _npc_status_label == null:
		return
	var car_str  := "car at (%d,%d)" % [_npc_cell.x, _npc_cell.y] if _npc_cell.x >= 0 else "no car"
	_npc_status_label.text = "NPC: %s · %d pts" % [car_str, _npc_path_points.size()]


# ══════════════════════════════════════════════════════════════════════════════
#  BULK CLEAR
# ══════════════════════════════════════════════════════════════════════════════

func _clear_roads() -> void:
	for n in _tile_nodes.values():
		n.queue_free()
	_tile_nodes.clear()
	_grid.clear()
	_refresh_status()

func _clear_props() -> void:
	for n in _duck_nodes.values():
		n.queue_free()
	_duck_nodes.clear()
	_duck_cells.clear()
	for n in _sign_nodes.values():
		n.queue_free()
	_sign_nodes.clear()
	for n in _sign_indicator_nodes.values():
		n.queue_free()
	_sign_indicator_nodes.clear()
	_sign_corners.clear()
	_refresh_status()


# ══════════════════════════════════════════════════════════════════════════════
#  STATUS
# ══════════════════════════════════════════════════════════════════════════════

func _refresh_status() -> void:
	_status_label.text = "Roads: %d  Ducks: %d  Signs: %d" % [
		_grid.size(), _duck_cells.size(), _sign_corners.size()
	]
	if _start_cell.x >= 0:
		var wx := (_start_cell.x + 0.5) * TILE_SIZE
		var wz := (_start_cell.y + 0.5) * TILE_SIZE
		_start_coord_label.text = "Start: col %d, row %d\n  pos (%.3f, 0, %.3f)" % [
			_start_cell.x, _start_cell.y, wx, wz
		]
	else:
		_start_coord_label.text = "Start: not set"


# ══════════════════════════════════════════════════════════════════════════════
#  MAP FILE LIST
# ══════════════════════════════════════════════════════════════════════════════

func _refresh_map_list() -> void:
	_map_dropdown.clear()
	for name in MapMakerFileIO.list_maps(MAPS_DIR):
		_map_dropdown.add_item(name)

# ══════════════════════════════════════════════════════════════════════════════
#  SAVE / LOAD
# ══════════════════════════════════════════════════════════════════════════════

func _build_map_data() -> MapData:
	var data             := MapData.new()
	data.tile_size        = TILE_SIZE
	data.npc_lane_offset  = NPC_LANE_OFFSET
	data.grid_w           = GRID_W
	data.grid_h           = GRID_H
	data.tiles            = _grid.duplicate(true)
	data.duck_cells       = _duck_cells.duplicate(true)
	data.sign_corners     = _sign_corners.duplicate(true)
	data.start_cell       = _start_cell
	data.start_rot        = _start_rot
	data.npc_cell         = _npc_cell
	data.npc_rot          = _npc_rot
	data.npc_path_points  = _npc_path_points.duplicate()
	return data

func _apply_map_data(data: MapData) -> void:
	for cell in data.tiles.keys():
		var e = data.tiles[cell]
		_place_tile(cell, e["type"], e["rot"])
	for cell in data.duck_cells.keys():
		_place_duck(cell, data.duck_cells[cell]["rot"])
	for corner in data.sign_corners.keys():
		var e = data.sign_corners[corner]
		_place_sign(corner, e["type"], e["rot"])
	if data.start_cell.x >= 0:
		_set_start_at(data.start_cell, data.start_rot)
	if data.npc_cell.x >= 0:
		_set_npc(data.npc_cell, data.npc_rot)
	for p in data.npc_path_points:
		_npc_path_points.append(p)
	_rebuild_path_visuals()
	_refresh_npc_status()


func _save_map() -> void:
	var map_name := _name_edit.text.strip_edges()
	if map_name.is_empty():
		_status_label.text = "Enter a map name first!"
		return
	var err := MapMakerFileIO.save(_build_map_data(), map_name, MAPS_DIR, ACTIVE_MAP_PATH)
	if err != "":
		_status_label.text = err
		return
	_status_label.text = "Saved '%s'!" % map_name
	_refresh_map_list()
	for i in range(_map_dropdown.item_count):
		if _map_dropdown.get_item_text(i) == map_name:
			_map_dropdown.select(i)
			break


func _load_selected_map() -> void:
	if _map_dropdown.item_count == 0:
		_status_label.text = "No saved maps found"
		return
	var map_name := _map_dropdown.get_item_text(_map_dropdown.selected)
	_load_from_file(MAPS_DIR + "/" + map_name + ".json", map_name)


func _load_from_file(path: String, display_name: String) -> void:
	var data := MapMakerFileIO.load_file(path)
	if data == null:
		_status_label.text = "Load FAILED!"
		return
	_clear_roads()
	_clear_props()
	_clear_start_visual()
	_start_cell = Vector2i(-1, -1)
	_clear_npc_visual()
	_npc_cell   = Vector2i(-1, -1)
	_clear_npc_path()
	_apply_map_data(data)
	_name_edit.text    = display_name
	_status_label.text = "Loaded '%s'" % display_name



func _delete_selected_map() -> void:
	if _map_dropdown.item_count == 0:
		_status_label.text = "No maps to delete"
		return
	var map_name := _map_dropdown.get_item_text(_map_dropdown.selected)
	var err := MapMakerFileIO.delete_map(map_name, MAPS_DIR)
	_status_label.text = ("Deleted: " + map_name) if err == "" else err
	if err == "":
		_refresh_map_list()


# ══════════════════════════════════════════════════════════════════════════════
#  EXPORT AS .tscn SCENE
# ══════════════════════════════════════════════════════════════════════════════

func _export_as_scene() -> void:
	var map_name := _name_edit.text.strip_edges()
	if map_name.is_empty():
		_status_label.text = "Enter a map name first!"
		return
	var data := _build_map_data()
	if data.tiles.is_empty() and data.duck_cells.is_empty() and data.sign_corners.is_empty():
		_status_label.text = "Nothing to export!"
		return
	var err := MapMakerExporter.export_scene(data, map_name, _tile_scenes, _obj_scenes)
	_status_label.text = err if err != "" else "Saved!"
