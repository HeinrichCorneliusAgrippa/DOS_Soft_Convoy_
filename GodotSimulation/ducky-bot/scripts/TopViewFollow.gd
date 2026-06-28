extends Camera3D

@export var follow_speed: float = 5.0

var _target: Node3D = null

func _ready() -> void:
	_target = get_tree().current_scene.get_node_or_null("DuckieBot")

func _process(delta: float) -> void:
	if _target == null:
		return
	var goal = Vector3(_target.global_position.x, global_position.y, _target.global_position.z)
	global_position = global_position.lerp(goal, follow_speed * delta)
