extends PathFollow3D
## NPC leader for the convoy follow scene. Unlike path_follow_3d.gd it stays
## parked until the camera stream to Python is up (the follower's agent starts
## right after that accept) plus a short grace, so the follower departs with
## the leader's back marker in view instead of an empty road.

@export var speed: float = 0.12
@export var start_grace_s: float = 4.0
# Corner profile, mirroring the real lead bot's stop-and-slow-after-turn
# behaviour: the follower is blind while the leader turns (the back marker
# leaves its camera), so slow into bends and dwell briefly after them to let
# the follower round the corner and re-lock before the gap opens.
@export var corner_speed: float = 0.10
@export var corner_exit_speed: float = 0.07
@export var corner_exit_slow_s: float = 3.0
@export var corner_bend_rad: float = 0.25

var _connected_for: float = -1.0
var _exit_slow_left: float = 0.0

@onready var _camera_streamer: Node = get_tree().current_scene.get_node_or_null("DuckieBot/CameraStreamer")

func _ready() -> void:
	rotation_mode = PathFollow3D.ROTATION_Y
	loop = true
	add_to_group("npc_leader")

func reset_leader() -> void:
	# Called (via group) when Python resets the game: re-park at the start of
	# the path and re-run the departure grace so the respawned follower gets
	# the same leader-ahead geometry as a fresh boot.
	progress = 0.0
	_connected_for = -1.0
	_exit_slow_left = 0.0

func _camera_up() -> bool:
	if _camera_streamer == null:
		return true  # no networked bot in the scene; just drive
	var tcp: StreamPeerTCP = _camera_streamer._tcp
	return tcp != null and tcp.get_status() == StreamPeerTCP.STATUS_CONNECTED

func _bend_ahead() -> float:
	var path := get_parent() as Path3D
	if path == null or path.curve == null:
		return 0.0
	var curve := path.curve
	var length := curve.get_baked_length()
	if length <= 0.5:
		return 0.0
	var p0 := curve.sample_baked(fposmod(progress, length))
	var p1 := curve.sample_baked(fposmod(progress + 0.15, length))
	var p2 := curve.sample_baked(fposmod(progress + 0.45, length))
	var a := Vector2(p1.x - p0.x, p1.z - p0.z)
	var b := Vector2(p2.x - p1.x, p2.z - p1.z)
	if a.length() < 1e-4 or b.length() < 1e-4:
		return 0.0
	return abs(a.angle_to(b))

func _process(delta: float) -> void:
	if not _camera_up():
		_connected_for = -1.0
		return
	if _connected_for < 0.0:
		_connected_for = 0.0
		print("[NPC] camera stream up; leader departs in ", start_grace_s, "s")
	_connected_for += delta
	if _connected_for < start_grace_s:
		return
	var v := speed
	if _bend_ahead() > corner_bend_rad:
		v = corner_speed
		_exit_slow_left = corner_exit_slow_s
	elif _exit_slow_left > 0.0:
		_exit_slow_left -= delta
		v = corner_exit_speed
	progress += v * delta
