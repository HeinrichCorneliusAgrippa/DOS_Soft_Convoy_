import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.absolute()

GODOT_DIR = PROJECT_ROOT / 'GodotSimulation' / 'ducky-bot'
SERVERS_DIR = PROJECT_ROOT / 'servers'
TASKS_DIR = PROJECT_ROOT / 'tasks'
DUCKIEBOT_DIR = PROJECT_ROOT / 'duckiebot'
CONFIG_DIR = PROJECT_ROOT / 'config'
SCRIPTS_DIR = PROJECT_ROOT / 'scripts'

GODOT_PROJECT = GODOT_DIR
GODOT_SCENES = {
    'braitenberg': 'res://scenes/braitenberg.tscn',
    'visual_lane_servoing': 'res://scenes/maps/lane_follower.tscn',
    'introduction': 'res://scenes/maps/introduction.tscn',
    'modcon': 'res://scenes/maps/Modconpath.tscn',
    'navigator': 'res://scenes/maps/map_follower.tscn',
    'object_detection': 'res://scenes/maps/lane_detect.tscn',
    # Digital twin of the physical KIU track; the follow variant adds a
    # path-driven NPC leader carrying the circle-grid back marker.
    'project_lead': 'res://scenes/maps/test1_actual_map_kiu.tscn',
    'project_follow': 'res://scenes/maps/test1_actual_map_kiu_follow.tscn',
}

DEFAULT_WEB_PORT = 5000
DEFAULT_DEPLOY_PORT = 8000
DEFAULT_CAMERA_PORT = 5001
DEFAULT_WHEEL_PORT = 5002


def get_task_scene(task_name):
    return GODOT_SCENES.get(task_name, f'res://scenes/{task_name}.tscn')


def get_task_dir(task_name):
    return TASKS_DIR / task_name


def get_task_server(task_name):
    return SERVERS_DIR / task_name / 'real_server.py'


def get_virtual_server(task_name):
    return SERVERS_DIR / task_name / 'virtual_server.py'
