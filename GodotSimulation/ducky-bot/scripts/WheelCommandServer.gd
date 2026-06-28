extends Node

var port: int = 5002

var server := TCPServer.new()
var peer: StreamPeerTCP = null

var left_cmd: float = 0.0
var right_cmd: float = 0.0

# Game state (set by robot on collision)
var game_over: bool = false
var final_survival_time: float = 0.0
var final_distance_traveled: float = 0.0
var final_distance_from_start: float = 0.0

# Push the state (incl. robot pose) to Python at this interval so the sim UI
# can draw a live map without having to request it.
const STATE_PUSH_INTERVAL_S: float = 0.1
var _state_push_timer: float = 0.0

# Outgoing frames queue here and drain with put_partial_data: a stalled or
# absent Python reader must never block the main thread (put_data is the
# blocking variant). Past the cap the reader is considered gone and dropped.
const TX_BUFFER_CAP: int = 65536
var _tx_buffer: PackedByteArray = PackedByteArray()

func _ready() -> void:
	var parent = get_parent()
	var base_port: int = parent.WheelPort
	var max_attempts: int = 20
	var bound: bool = false

	for i in range(max_attempts):
		port = base_port + i
		var err: int = server.listen(port)
		if err == OK:
			bound = true
			break
		else:
			# Port busy, stop the failed server and try the next one
			server.stop()
			server = TCPServer.new()

	if not bound:
		push_error("[WheelServer] Could not bind any port in range %d-%d" % [base_port, base_port + max_attempts - 1])
		return

	print("[Godot] Wheel server listening on port ", port)

	# Write the actual port to a JSON file so Python can discover it
	var port_file: String = parent.port_file_path
	if port_file != "":
		var f := FileAccess.open(port_file, FileAccess.WRITE)
		if f:
			f.store_string(JSON.stringify({"wheel_port": port}))
			f.close()
			print("[WheelServer] Wrote port file: ", port_file)
		else:
			push_warning("[WheelServer] Could not write port file: %s" % port_file)

func set_game_over(survival_time: float, distance_traveled: float, distance_from_start: float) -> void:
	game_over = true
	final_survival_time = survival_time
	final_distance_traveled = distance_traveled
	final_distance_from_start = distance_from_start

	# Send game over message to Python
	_send_game_over()

func _send_game_over() -> void:
	if peer == null:
		return

	var msg = {
		"type": "game_over",
		"survival_time": final_survival_time,
		"distance_traveled": final_distance_traveled,
		"distance_from_start": final_distance_from_start
	}
	_queue_send(msg)
	print("[WheelServer] Sent game_over to Python")

func _queue_send(msg: Dictionary) -> void:
	if peer == null:
		return
	if _tx_buffer.size() > TX_BUFFER_CAP:
		print("[WheelServer] TX backlog (reader stalled); dropping client")
		peer = null
		left_cmd = 0.0
		right_cmd = 0.0
		_tx_buffer = PackedByteArray()
		return
	var payload: PackedByteArray = JSON.stringify(msg).to_utf8_buffer()
	var length: int = payload.size()
	var frame := PackedByteArray()
	frame.append((length >> 24) & 0xFF)
	frame.append((length >> 16) & 0xFF)
	frame.append((length >> 8) & 0xFF)
	frame.append(length & 0xFF)
	frame.append_array(payload)
	_tx_buffer.append_array(frame)

func _drain_tx() -> void:
	if peer == null or _tx_buffer.size() == 0:
		return
	var res: Array = peer.put_partial_data(_tx_buffer)
	if int(res[0]) != OK:
		return  # disconnects are handled by the status check in _process
	var sent: int = int(res[1])
	if sent > 0:
		_tx_buffer = _tx_buffer.slice(sent)

func _process(_delta: float) -> void:
	# Accept client
	if peer == null and server.is_connection_available():
		peer = server.take_connection()
		print("[Godot] Wheel client connected")
		# Reset game state on new connection
		game_over = false

	if peer == null:
		return

	peer.poll()

	_state_push_timer += _delta
	if _state_push_timer >= STATE_PUSH_INTERVAL_S:
		_state_push_timer = 0.0
		_send_state()
	_drain_tx()

	# if socket closed, drop it
	if peer == null:
		return
	var st: int = peer.get_status()
	if st == StreamPeerTCP.STATUS_NONE or st == StreamPeerTCP.STATUS_ERROR:
		print("[WheelServer] client disconnected (status=", st, ")")
		peer = null
		left_cmd = 0.0
		right_cmd = 0.0
		_tx_buffer = PackedByteArray()
		return

	# Read: uint32 length + JSON bytes
	while peer.get_available_bytes() >= 4:

		var res_len: Array = peer.get_data(4)
		var err_len: int = int(res_len[0])
		if err_len != OK:
			print("[WheelServer] len read error:", err_len)
			return

		var len_bytes: PackedByteArray = res_len[1]

		# Convert big-endian manually
		var msg_len: int = (int(len_bytes[0]) << 24) | (int(len_bytes[1]) << 16) | (int(len_bytes[2]) << 8) | int(len_bytes[3])

		# sanity check
		if msg_len <= 0 or msg_len > 4096:
			print("[WheelServer] BAD msg_len=", msg_len, " -> dropping client")
			peer = null
			left_cmd = 0.0
			right_cmd = 0.0
			return

		if peer.get_available_bytes() < msg_len:
			return

		var res: Array = peer.get_data(msg_len)
		var err2: int = int(res[0])
		if err2 != OK:
			push_warning("[WheelServer] get_data error: %s" % err2)
			return

		var data: PackedByteArray = res[1]
		var text: String = data.get_string_from_utf8()

		# Ignore non-JSON garbage
		if text.length() == 0 or text[0] != "{":
			continue

		var obj: Variant = JSON.parse_string(text)
		if typeof(obj) != TYPE_DICTIONARY:
			continue

		var d: Dictionary = obj
		var msg_type = str(d.get("type", ""))

		if msg_type == "wheels":
			# Don't accept wheel commands if game is over
			if not game_over:
				left_cmd = float(d.get("left", 0.0))
				right_cmd = float(d.get("right", 0.0))

		elif msg_type == "reset":
			# Reset game - WheelServer is a direct child of the robot node
			var robot = get_parent()
			if robot and robot.has_method("reset_game"):
				robot.reset_game()
				game_over = false
				# No-op in scenes without an NPC leader; in the convoy follow
				# scene it re-parks the leader ahead of the respawned follower.
				get_tree().call_group("npc_leader", "reset_leader")
				print("[WheelServer] Game reset by Python")

		elif msg_type == "remove_objects":
			var filter_str: String = str(d.get("filter", "")).to_lower()
			if filter_str != "":
				_remove_matching(get_tree().get_root(), filter_str)

		elif msg_type == "change_scene":
			var scene_path: String = str(d.get("scene", ""))
			if scene_path != "":
				print("[WheelServer] Changing scene to: ", scene_path)
				get_tree().change_scene_to_file.call_deferred(scene_path)

		elif msg_type == "get_state":
			# Send current game state to Python
			_send_state()

func _remove_matching(node: Node, filter: String) -> bool:
	for child in node.get_children():
		if child == get_parent():
			# Don't free the robot node itself; still recurse into it
			if _remove_matching(child, filter):
				return true
			continue
		if child.name.to_lower().contains(filter):
			child.queue_free()
			print("[WheelServer] Removed: ", child.name)
			return true
		if _remove_matching(child, filter):
			return true
	return false

func _send_state() -> void:
	if peer == null:
		return

	var robot = get_parent()
	var state = {}

	if robot and robot.has_method("get_game_state"):
		state = robot.get_game_state()
		if game_over:
			# Keep the final stats frozen: get_game_state() computes
			# survival_time from the clock, which keeps counting.
			state["survival_time"] = final_survival_time
			state["total_distance"] = final_distance_traveled
			state["distance_from_start"] = final_distance_from_start
	else:
		state = {
			"game_over": game_over,
			"survival_time": final_survival_time,
			"distance_traveled": final_distance_traveled,
			"distance_from_start": final_distance_from_start
		}

	# Include the NPC leader's pose when the scene has one (convoy follow
	# scene), so the sim UI can draw it and the gap is measurable.
	var npcs := get_tree().get_nodes_in_group("npc_leader")
	if npcs.size() > 0:
		var npc := npcs[0] as Node3D
		if npc != null:
			state["npc_x"] = npc.global_position.x
			state["npc_z"] = npc.global_position.z

	state["type"] = "state"
	_queue_send(state)
