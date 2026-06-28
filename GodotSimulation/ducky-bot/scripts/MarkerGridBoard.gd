extends MeshInstance3D
## Procedural circle-grid back marker (black dots on white) for the NPC leader.
## The follower's MarkerGridTracker (tasks/project/packages/marker_grid.py)
## finds this with cv2.findCirclesGrid. Built at runtime so adding it needs no
## texture-import step (headless sim runs don't re-import assets).

# Sized so the follower's tracker resolves the dots at convoy distances:
# with the 640x480 sim camera (~75 deg fov) and the tracker's 0.5 downscale,
# these dots stay above SimpleBlobDetector's min area out to ~1.5 m, and the
# follow config's span thresholds (stop 70 px / safe 18 px) map to ~0.25 m
# and ~0.95 m gaps.
@export var cols: int = 7
@export var rows: int = 3
@export var spacing_m: float = 0.042
@export var dot_radius_m: float = 0.013
@export var margin_m: float = 0.024
@export var px_per_m: float = 1500.0

func _ready() -> void:
	var w_m: float = (cols - 1) * spacing_m + 2.0 * margin_m
	var h_m: float = (rows - 1) * spacing_m + 2.0 * margin_m
	var w_px: int = int(round(w_m * px_per_m))
	var h_px: int = int(round(h_m * px_per_m))

	# Mipmaps matter: without them the dots alias into merged squares at
	# distance and the blob detector's circularity filter rejects them.
	var img := Image.create(w_px, h_px, true, Image.FORMAT_RGB8)
	img.fill(Color.WHITE)
	var r_px: float = dot_radius_m * px_per_m
	for row in range(rows):
		for col in range(cols):
			var cx: float = (margin_m + col * spacing_m) * px_per_m
			var cy: float = (margin_m + row * spacing_m) * px_per_m
			_fill_circle(img, cx, cy, r_px)
	img.generate_mipmaps()

	var quad := QuadMesh.new()
	quad.size = Vector2(w_m, h_m)
	mesh = quad

	var mat := StandardMaterial3D.new()
	mat.albedo_texture = ImageTexture.create_from_image(img)
	# Unshaded keeps the dots black and the board white regardless of scene
	# lighting, which is what the blob detector's thresholds want.
	mat.shading_mode = BaseMaterial3D.SHADING_MODE_UNSHADED
	material_override = mat

func _fill_circle(img: Image, cx: float, cy: float, r: float) -> void:
	for y in range(int(cy - r) - 1, int(cy + r) + 2):
		for x in range(int(cx - r) - 1, int(cx + r) + 2):
			if x < 0 or y < 0 or x >= img.get_width() or y >= img.get_height():
				continue
			var dx: float = x + 0.5 - cx
			var dy: float = y + 0.5 - cy
			if dx * dx + dy * dy <= r * r:
				img.set_pixel(x, y, Color.BLACK)
