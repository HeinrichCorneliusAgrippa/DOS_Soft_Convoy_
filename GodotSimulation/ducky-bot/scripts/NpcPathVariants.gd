class_name NpcPathVariants

static func get_points(
		cell_center: Vector3,
		variant:     int,
		tile_size:   float,
		lane_offset: float) -> Array:

	var L  := lane_offset
	var H  := tile_size * 0.5
	var Rr := H - L              # right-turn arc radius (inner lane)
	var Rl := H + L              # left-turn  arc radius (outer lane)
	var rp := Rr / sqrt(2.0)   # right-turn 45° chord projection

	# 2-D [dx, dz] offsets from cell centre
	var d: Array = []
	if   variant == 0: d = [[  L,  0.0]]
	elif variant == 1: d = [[ -L,  0.0]]
	elif variant == 2: d = [[0.0,   L ]]
	elif variant == 3: d = [[0.0,  -L ]]
	elif variant == 4: d = [[L, H], [H - rp, H - rp], [H, L]]
	elif variant == 5: d = [[-H, L], [-H + rp, H - rp], [-L, H]]
	elif variant == 6: d = [[-L, -H], [-H + rp, -H + rp], [-H, -L]]
	elif variant == 7: d = [[H, -L], [H - rp, -H + rp], [L, -H]]
	elif variant == 8:  d = _arc5(-H,  H,   0.0, Rl)   # left:  N→W
	elif variant == 9:  d = _arc5(-H, -H,  90.0, Rl)   # left:  E→N
	elif variant == 10: d = _arc5( H, -H, 180.0, Rl)   # left:  S→E
	elif variant == 11: d = _arc5( H,  H, 270.0, Rl)   # left:  W→S
	else: d = [[0.0, 0.0]]

	var out: Array = []
	for p in d:
		out.append(Vector3(cell_center.x + p[0], 0.091, cell_center.z + p[1]))
	return out


## Five equally-spaced points along a 90° arc.
## Sweeps from a_start to a_start − 90°, which is a left turn in world space.
## (cx, cz) is the arc centre offset from the cell centre.
static func _arc5(cx: float, cz: float, a_start: float, R: float) -> Array:
	var pts: Array = []
	for i in range(5):
		var a := deg_to_rad(a_start - i * 22.5)
		pts.append([cx + R * cos(a), cz + R * sin(a)])
	return pts
