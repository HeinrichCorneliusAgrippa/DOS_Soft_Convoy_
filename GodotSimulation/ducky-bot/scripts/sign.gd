@tool
extends Node3D

@export var sign_texture: Texture2D:
	set(t):
		sign_texture = t
		if Engine.is_editor_hint() and t != null:
			_fix_import(t)
		_apply()

func _ready() -> void:
	_apply()

func _apply() -> void:
	if sign_texture == null or not is_inside_tree():
		return
	var mi := $texture as MeshInstance3D
	if mi == null or mi.mesh == null:
		return
	var mat: StandardMaterial3D = mi.get_surface_override_material(0) as StandardMaterial3D
	if mat == null:
		var base: StandardMaterial3D = mi.mesh.surface_get_material(0) as StandardMaterial3D
		mat = base.duplicate() as StandardMaterial3D if base != null else StandardMaterial3D.new()
		mi.set_surface_override_material(0, mat)
	mat.albedo_color   = Color.WHITE
	mat.texture_filter = BaseMaterial3D.TEXTURE_FILTER_NEAREST
	mat.uv1_scale      = Vector3(1.0, 1.0, 1.0)
	mat.uv1_offset     = Vector3.ZERO
	var img := sign_texture.get_image()
	mat.albedo_texture = ImageTexture.create_from_image(img)


func _fix_import(tex: Texture2D) -> void:
	var import_path := tex.resource_path + ".import"
	var f := FileAccess.open(import_path, FileAccess.READ)
	if f == null:
		return
	var content := f.get_as_text()
	f.close()
	var changed := false
	for val in ["1", "2"]:
		var key: String = '"detect_3d/compress_to": ' + val
		if content.contains(key):
			content = content.replace(key, '"detect_3d/compress_to": 0')
			changed = true
	if not changed:
		return
	var fw := FileAccess.open(import_path, FileAccess.WRITE)
	fw.store_string(content)
	fw.close()
	EditorInterface.get_resource_filesystem().reimport_files([tex.resource_path])
