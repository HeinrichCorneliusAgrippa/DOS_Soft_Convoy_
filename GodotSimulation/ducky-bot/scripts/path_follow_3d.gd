extends PathFollow3D

@export var speed: float = 0.2
var running: bool = true

func _ready() -> void:
	rotation_mode = PathFollow3D.ROTATION_Y
	loop = true

func _process(delta: float) -> void:
	if running:
		progress += speed * delta
