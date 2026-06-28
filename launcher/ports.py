import socket
import time
import json


def find_available_port(start=5000, max_attempts=20, exclude=None):
    """Find a free TCP port by test-binding, starting from `start`."""
    if exclude is None:
        exclude = set()

    for offset in range(max_attempts):
        port = start + offset
        if port in exclude:
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("", port))
                return port
        except OSError:
            continue

    raise RuntimeError(
        f"Could not find a free port in range {start}-{start + max_attempts - 1}"
    )


def wait_for_port_file(path, timeout=15, poll_interval=0.25):
    """Block until Godot writes its JSON port file, or timeout."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with open(path, "r") as f:
                data = json.load(f)
            if isinstance(data, dict) and data:
                return data
        except (FileNotFoundError, json.JSONDecodeError, ValueError):
            pass
        time.sleep(poll_interval)

    raise TimeoutError(
        f"Godot port file '{path}' not found after {timeout}s. "
        "Is Godot running?"
    )
                                                