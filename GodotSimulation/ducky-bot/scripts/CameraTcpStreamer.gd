extends Node
# This node streams the bot's Camera3D view via TCP to Python virtual server
# The camera is in a SubViewport that shares the parent scene's 3D world

@export var host: String = "127.0.0.1"
@export var target_fps: float = 30.0
@export var resize_width: int = 0
@export var resize_height: int = 0
@export var jpeg_quality: float = 0.75

var _tcp: StreamPeerTCP = StreamPeerTCP.new()
var _accum: float = 0.0
var _reconnect_accum: float = 0.0
var port: int = 5001
var viewport: SubViewport = null
var camera: Camera3D = null
var bot: CharacterBody3D = null

# Store the initial local transform of the camera relative to bot
var camera_local_transform: Transform3D

func _ready() -> void:
	# Disable low processor mode to keep full FPS even when unfocused
	var parent = get_parent()
	port = parent.CameraPort
	OS.low_processor_usage_mode = false
	Engine.max_fps = 60

	# Store reference to the bot
	bot = parent

	# Find the SubViewport sibling
	viewport = parent.get_node("BotCameraViewport")
	if viewport == null:
		print("[Streamer] ❌ Could not find BotCameraViewport!")
		return

	# Find the camera inside the viewport
	camera = viewport.get_node("BotCamera")
	if camera == null:
		print("[Streamer] ❌ Could not find BotCamera!")
		return

	# Store the camera's local transform (offset from bot)
	camera_local_transform = camera.transform

	# CRITICAL: Share the parent scene's 3D world with the SubViewport
	# This allows the bot's camera to see the same ducks/environment
	viewport.world_3d = get_viewport().world_3d
	print("[Streamer] ✅ SubViewport now shares parent world_3d")

	print("[Godot] Streamer READY ✅  host=%s port=%d (JPEG mode)" % [host, port])
	print("[Godot] Low processor mode disabled - will stream even when unfocused")
	_connect()


func _notification(what: int) -> void:
	# Keep running at full speed even when window loses focus
	if what == NOTIFICATION_APPLICATION_FOCUS_OUT:
		OS.low_processor_usage_mode = false
	elif what == NOTIFICATION_WM_WINDOW_FOCUS_OUT:
		OS.low_processor_usage_mode = false

func _connect() -> void:
	_tcp = StreamPeerTCP.new()
	var _err := _tcp.connect_to_host(host, port)

func _process(delta: float) -> void:
	# Update camera position to follow the bot
	if camera != null and bot != null:
		# Set camera's global transform = bot's global transform * camera's local offset
		camera.global_transform = bot.global_transform * camera_local_transform

	_tcp.poll()
	var st := _tcp.get_status()

	if st != StreamPeerTCP.STATUS_CONNECTED:
		_accum += delta
		if _accum > 2.0:
			_accum = 0.0
			_connect()
		return

	# Connected: send frames at target FPS
	_accum += delta
	var period: float = 1.0 / max(target_fps, 1.0)
	if _accum >= period:
		_accum = 0.0
		_send_frame()

func _send_frame() -> void:
	if viewport == null:
		return

	# Capture from the bot's SubViewport (not the main window viewport)
	var img: Image = viewport.get_texture().get_image()
	if img == null:
		print("[Streamer] ❌ get_texture() returned null!")
		return

	if img.is_empty():
		print("[Streamer] ❌ Image is empty!")
		return

	if resize_width > 0 and resize_height > 0:
		img.resize(resize_width, resize_height, Image.INTERPOLATE_BILINEAR)

	# Encode as JPEG
	var buf: PackedByteArray = img.save_jpg_to_buffer(jpeg_quality)
	var n: int = buf.size()

	if n <= 0:
		print("[Streamer] ❌ JPEG encode failed")
		return

	# ✅ SEND THE DATA  (put_u32 returns void, only put_data returns Error)
	var e1 = _tcp.put_data("GPNG".to_utf8_buffer())
	_tcp.put_u32(n)
	var e3 = _tcp.put_data(buf)
	if e1 != OK or e3 != OK:
		print("[Streamer] ❌ Send failed (%d/%d) - reconnecting" % [e1, e3])
		_accum = 0.0
		_connect()
		return

	# Optional: print every 30 frames to confirm it's working
	if Engine.get_process_frames() % 30 == 0:
		print("[Streamer] ✅ Sent frame: %d bytes JPEG (size: %dx%d)" % [n, img.get_width(), img.get_height()])
