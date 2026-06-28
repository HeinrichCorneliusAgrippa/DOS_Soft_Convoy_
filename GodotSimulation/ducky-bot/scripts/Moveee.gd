extends CharacterBody3D

@export var wheel_radius: float = 0.0318   # meters
@export var baseline: float = 0.10         # meters
@export var max_speed: float = 1.0         # m/s (simulation speed — 5× faster than a real Duckiebot)
@export var max_turn_rate: float = 8.0   # Max radians/sec (match omega_max from calibration)
@export var WheelPort = 5002
@export var CameraPort = 5001
@export var  RotateOnStart = false

var port_file_path: String = ""
var gravity: float = 9.8
var _current_omega: float = 0.0

@onready var wheel_server = get_node("WheelServer")

func _enter_tree() -> void:
	for arg in OS.get_cmdline_user_args():
		if arg.begins_with("--camera-port="):
			CameraPort = int(arg.split("=")[1])
		elif arg.begins_with("--wheel-port="):
			WheelPort = int(arg.split("=")[1])
		elif arg.begins_with("--port-file="):
			port_file_path = arg.split("=")[1]
	print("[Robot] Ports from CLI: camera=", CameraPort, " wheel_hint=", WheelPort, " port_file=", port_file_path)

var initial_position: Vector3
var initial_rotation: Vector3
var start_position: Vector3
var start_time: float
var game_over: bool = false
var collision_duck_name: String = ""
var total_distance: float = 0.0
var last_position: Vector3

var _minimum_player: AudioStreamPlayer = null
var _current_pitch: float = 1.0

func _ready() -> void:
	gravity = ProjectSettings.get_setting("physics/3d/default_gravity", 9.8)
	_minimum_player = get_tree().current_scene.get_node_or_null("Minimum")
	if _minimum_player:
		_minimum_player.pitch_scale = _current_pitch

	if RotateOnStart:
		rotation.y = randf_range(0.0, TAU)

	initial_position = global_position
	initial_rotation = rotation

	start_position = global_position
	last_position = global_position
	start_time = Time.get_unix_time_from_system()
	print("[Robot] Started at position: ", start_position, " rotation.y=", rad_to_deg(rotation.y))

func _physics_process(delta: float) -> void:
	if game_over:
		velocity = Vector3.ZERO
		return

	var left: float = wheel_server.left_cmd
	var right: float = wheel_server.right_cmd

	var v_left: float = left * max_speed
	var v_right: float = right * max_speed

	var v: float = (v_right + v_left) * 0.5

	var omega: float = (v_right - v_left) / baseline
	omega = clamp(omega, -max_turn_rate, max_turn_rate)
	_current_omega = omega
	rotate_y(_current_omega * delta)

	if is_on_floor():
		velocity.y = 0.0
	else:
		velocity.y -= gravity * delta

	velocity.x = -global_transform.basis.z.x * v
	velocity.z = -global_transform.basis.z.z * v

	move_and_slide()

	if _minimum_player:
		var target_pitch: float = 1.0 + max(0.0, v) / max_speed * 0.15
		_current_pitch = lerp(_current_pitch, target_pitch, delta * 3.0)
		_minimum_player.pitch_scale = _current_pitch

	for i in get_slide_collision_count():
		var col = get_slide_collision(i)
		var body = col.get_collider()
		if body is RigidBody3D:
			body.apply_central_impulse(-col.get_normal() * v * 2.0)

	var moved = global_position.distance_to(last_position)
	total_distance += moved
	last_position = global_position

	_check_duck_collision()

func _check_duck_collision() -> void:
	for i in get_slide_collision_count():
		var col = get_slide_collision(i)
		var body = col.get_collider()
		if body != null and body.is_in_group("ducks"):
			_on_duck_collision(body.name)
			return

func _on_duck_collision(duck_name: String) -> void:
	game_over = true
	collision_duck_name = duck_name

	var survival_time = Time.get_unix_time_from_system() - start_time
	var distance_from_start = start_position.distance_to(global_position)

	print("")
	print("=".repeat(50))
	print("[GAME OVER] Hit: ", duck_name)
	print("[STATS] Survival time: %.2f seconds" % survival_time)
	print("[STATS] Distance traveled: %.2f meters" % total_distance)
	print("[STATS] Distance from start: %.2f meters" % distance_from_start)
	print("=".repeat(50))
	print("")

	wheel_server.set_game_over(survival_time, total_distance, distance_from_start)

func get_game_state() -> Dictionary:
	# heading_rad is the angle of the forward (-basis.z) vector: atan2(fwd_x, fwd_z).
	return {
		"game_over": game_over,
		"survival_time": Time.get_unix_time_from_system() - start_time,
		"total_distance": total_distance,
		"distance_from_start": start_position.distance_to(global_position),
		"collision_duck": collision_duck_name,
		"pose_x": global_position.x,
		"pose_z": global_position.z,
		"heading_rad": atan2(-global_transform.basis.z.x, -global_transform.basis.z.z)
	}

func reset_game() -> void:
	global_position = initial_position
	if RotateOnStart:
		rotation = Vector3(initial_rotation.x, randf_range(0.0, TAU), initial_rotation.z)
	else:
		rotation = Vector3(initial_rotation)
	velocity = Vector3.ZERO

	game_over = false
	_current_omega = 0
	collision_duck_name = ""
	start_position = initial_position
	last_position = initial_position
	start_time = Time.get_unix_time_from_system()
	total_distance = 0.0
