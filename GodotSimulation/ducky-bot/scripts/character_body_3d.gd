extends CharacterBody3D

@export var move_speed: float = 0.2
@export var turn_speed: float = 1.0

func _physics_process(delta: float) -> void:
	var forward_input := 0.0
	var turn_input := 0.0

	# -------------------------------------
	# FORWARD / BACKWARD (W/S)
	# -------------------------------------
	if Input.is_action_pressed("move_forward"):
		forward_input = -1.0
	elif Input.is_action_pressed("move_backward"):
		forward_input = 1.0

	# -------------------------------------
	# LEFT / RIGHT — TURN IN PLACE (A/D)
	# -------------------------------------
	if Input.is_action_pressed("move_left"):
		turn_input = -1.0      # rotate left
	elif Input.is_action_pressed("move_right"):
		turn_input = 1.0       # rotate rightw

	# -------------------------------------
	# APPLY ROTATION (turns in place)
	# -------------------------------------
	rotation.y += turn_input * turn_speed * delta

	# -------------------------------------
	# MOVE FORWARD BASED ON CURRENT ROTATION
	# -------------------------------------
	var forward_dir := -transform.basis.z
	velocity = forward_dir * (forward_input * move_speed)

	move_and_slide()
