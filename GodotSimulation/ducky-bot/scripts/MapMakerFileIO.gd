class_name MapMakerFileIO
# ─────────────────────────────────────────────────────────────────────────────
# Pure file I/O for map JSON files.  No scene-tree or UI state here.
# Every function is static and returns a String error message (empty = OK),
# or the result type where applicable.
# ─────────────────────────────────────────────────────────────────────────────


## Save map to maps_dir/<map_name>.json and overwrite the active map file.
static func save(data: MapData, map_name: String,
		maps_dir: String, active_path: String) -> String:
	var json := JSON.stringify(data.to_dict(map_name), "\t")

	var f := FileAccess.open(maps_dir + "/" + map_name + ".json", FileAccess.WRITE)
	if f == null:
		return "Save FAILED!"
	f.store_string(json)
	f.close()

	# Also overwrite the active map so the sim picks it up immediately
	var fa := FileAccess.open(active_path, FileAccess.WRITE)
	if fa:
		fa.store_string(json)
		fa.close()

	return ""


## Load and parse a map JSON file.  Returns null if the file cannot be read.
static func load_file(path: String) -> MapData:
	var f := FileAccess.open(path, FileAccess.READ)
	if f == null:
		return null
	var text := f.get_as_text()
	f.close()
	var d = JSON.parse_string(text)
	if not d is Dictionary:
		return null
	return MapData.from_dict(d)


## Copy map_name's JSON to active_path so the sim uses it next run.
static func set_active(map_name: String,
		maps_dir: String, active_path: String) -> String:
	var f := FileAccess.open(maps_dir + "/" + map_name + ".json", FileAccess.READ)
	if f == null:
		return "Cannot read: " + map_name
	var content := f.get_as_text()
	f.close()
	var fa := FileAccess.open(active_path, FileAccess.WRITE)
	if fa == null:
		return "Cannot write active map!"
	fa.store_string(content)
	fa.close()
	return ""


## Delete a map JSON file.  Returns "" on success, error message on failure.
static func delete_map(map_name: String, maps_dir: String) -> String:
	var abs := ProjectSettings.globalize_path(maps_dir + "/" + map_name + ".json")
	if DirAccess.remove_absolute(abs) == OK:
		return ""
	return "Delete failed!"


## Returns a sorted list of map base-names (no extension) found in maps_dir.
static func list_maps(maps_dir: String) -> Array:
	var names: Array = []
	var dir := DirAccess.open(maps_dir)
	if dir == null:
		return names
	dir.list_dir_begin()
	var fname := dir.get_next()
	while fname != "":
		if not dir.current_is_dir() and fname.ends_with(".json"):
			names.append(fname.get_basename())
		fname = dir.get_next()
	dir.list_dir_end()
	names.sort()
	return names


## Returns the map name stored in the active map file, or "" if none.
static func read_active_name(active_path: String) -> String:
	if not FileAccess.file_exists(active_path):
		return ""
	var f := FileAccess.open(active_path, FileAccess.READ)
	if f == null:
		return ""
	var d = JSON.parse_string(f.get_as_text())
	f.close()
	if d is Dictionary and d.has("name"):
		return str(d["name"])
	return "custom_map"
