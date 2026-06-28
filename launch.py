import logging
import sys
import os
import argparse
import subprocess
import importlib
import io
import tarfile
import tempfile
import platform
import zipfile
import stat
from pathlib import Path

import requests

from launcher.ports import find_available_port, wait_for_port_file

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

GODOT_PROJECT = os.path.join(PROJECT_ROOT, 'GodotSimulation', 'ducky-bot')
from launcher.config import GODOT_SCENES

GODOT_VERSION = "4.6"
GODOT_CACHE_DIR = os.path.join(Path.home(), '.cache', 'duckietown', 'godot')
GODOT_DOWNLOAD_URLS = {
    'Linux':  f'https://github.com/godotengine/godot/releases/download/{GODOT_VERSION}-stable/Godot_v{GODOT_VERSION}-stable_linux.x86_64.zip',
    'Windows': f'https://github.com/godotengine/godot/releases/download/{GODOT_VERSION}-stable/Godot_v{GODOT_VERSION}-stable_win64.exe.zip',
    'Darwin': f'https://github.com/godotengine/godot/releases/download/{GODOT_VERSION}-stable/Godot_v{GODOT_VERSION}-stable_macos.universal.zip',
}

godot_process = None


def _get_cached_godot_path():
    system = platform.system()
    if system == 'Windows':
        return os.path.join(GODOT_CACHE_DIR, f'Godot_v{GODOT_VERSION}-stable_win64.exe')
    elif system == 'Darwin':
        return os.path.join(GODOT_CACHE_DIR, 'Godot.app', 'Contents', 'MacOS', 'Godot')
    else:
        return os.path.join(GODOT_CACHE_DIR, f'Godot_v{GODOT_VERSION}-stable_linux.x86_64')


def download_godot():
    system = platform.system()
    url = GODOT_DOWNLOAD_URLS.get(system)
    if not url:
        print(f"   No auto-download available for {system}")
        return None

    cached_path = _get_cached_godot_path()
    if os.path.isfile(cached_path):
        return cached_path

    print(f"   Godot {GODOT_VERSION} not found. Downloading for {system}...")
    os.makedirs(GODOT_CACHE_DIR, exist_ok=True)
    zip_path = os.path.join(GODOT_CACHE_DIR, 'godot_download.zip')

    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()
        total_size = int(response.headers.get('content-length', 0))
        downloaded = 0
        with open(zip_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)
                if total_size > 0:
                    pct = min(100, downloaded * 100 // total_size)
                    mb_done = downloaded / (1024 * 1024)
                    mb_total = total_size / (1024 * 1024)
                    print(f"\r   Downloading: {mb_done:.1f}/{mb_total:.1f} MB ({pct}%)", end='', flush=True)
        print()
    except Exception as e:
        print(f"\n   Download failed: {e}")
        return None

    print("   Extracting...")
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            zf.extractall(GODOT_CACHE_DIR)
    except Exception as e:
        print(f"   Extraction failed: {e}")
        return None
    finally:
        try:
            os.remove(zip_path)
        except OSError:
            pass

    if system != 'Windows' and os.path.isfile(cached_path):
        os.chmod(cached_path, os.stat(cached_path).st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

    # macOS: strip Gatekeeper quarantine + patch Info.plist for background rendering
    if system == 'Darwin':
        print("   Removing macOS quarantine flag (Gatekeeper)...")
        try:
            subprocess.run(['xattr', '-dr', 'com.apple.quarantine', GODOT_CACHE_DIR],
                           check=True, capture_output=True)
            print("   Quarantine removed.")
        except Exception as e:
            print(f"   Warning: Could not remove quarantine: {e}")
            print(f"     xattr -dr com.apple.quarantine {GODOT_CACHE_DIR}")

        info_plist = os.path.join(GODOT_CACHE_DIR, 'Godot.app', 'Contents', 'Info.plist')
        if os.path.isfile(info_plist):
            print("   Patching Info.plist (disable App Nap + ProMotion throttling)...")
            try:
                for key, value in [('NSAppSleepDisabled', '-bool YES'),
                                   ('CADisableMinimumFrameDurationOnPhone', '-bool YES')]:
                    subprocess.run(['defaults', 'write', info_plist, key] + value.split(),
                                   check=True, capture_output=True)
                print("   Info.plist patched.")
            except Exception as e:
                print(f"   Warning: Could not patch Info.plist: {e}")

    if os.path.isfile(cached_path):
        print(f"   Cached at: {cached_path}")
        return cached_path

    print(f"   Warning: Expected binary not found at {cached_path}")
    print(f"   Extracted files: {os.listdir(GODOT_CACHE_DIR)}")
    return None


def find_godot():
    import shutil

    # Check PATH
    for name in ['godot', 'godot4', 'godot-4', 'Godot']:
        path = shutil.which(name)
        if path:
            return path

    # Common install locations
    common_paths = [
        '/usr/bin/godot', '/usr/bin/godot4', '/usr/bin/godot-mono',
        '/usr/local/bin/godot', '/usr/local/bin/godot4',
        os.path.expanduser('~/.local/bin/godot'),
        os.path.expanduser('~/.local/bin/godot4'),
        '/var/lib/flatpak/exports/bin/org.godotengine.Godot',
        'C:/Program Files/Godot/Godot.exe',
        'C:/Godot/Godot.exe',
        str(Path.home() / 'AppData/Local/Godot/Godot.exe'),
    ]
    for path in common_paths:
        if os.path.isfile(path):
            return path

    cached = _get_cached_godot_path()
    if os.path.isfile(cached):
        return cached

    return download_godot()


def launch_godot(godot_path=None, debug=False, camera_port=None, wheel_port_hint=None, port_file_path=None, scene=None, user_args=None):
    global godot_process

    if not godot_path:
        godot_path = find_godot()
        if not godot_path:
            print("❌ ERROR: Godot not found and auto-download failed!")
            print("   Install manually or use: --godot-path /path/to/godot")
            return False

    print(f"✅ Found Godot: {godot_path}")

    project_file = os.path.join(GODOT_PROJECT, 'project.godot')
    if not os.path.isfile(project_file):
        print(f"❌ ERROR: Godot project not found at {GODOT_PROJECT}")
        return False

    # Import assets on first run
    imported_dir = os.path.join(GODOT_PROJECT, '.godot', 'imported')
    needs_import = not (os.path.isdir(imported_dir) and
                        any(f.endswith(('.scn', '.res', '.mesh')) for f in os.listdir(imported_dir)))
    if needs_import:
        print("⏳ Importing Godot project assets (first run only)...")
        try:
            subprocess.run([godot_path, '--path', GODOT_PROJECT, '--import', '--headless'],
                           cwd=GODOT_PROJECT, timeout=120)
            print("✅ Asset import complete")
        except subprocess.TimeoutExpired:
            print("⚠️  Import timed out, continuing anyway...")
        except Exception as e:
            print(f"⚠️  Import failed: {e}, continuing anyway...")

    godot_scene = scene or GODOT_SCENES.get('braitenberg', 'res://scenes/braitenberg.tscn')

    # On macOS, force OpenGL3 (ANGLE) — Metal throttles rendering for occluded windows
    if platform.system() == 'Darwin':
        godot_cmd = [godot_path, '--rendering-driver', 'opengl3', '--path', GODOT_PROJECT, godot_scene]
    else:
        godot_cmd = [godot_path, '--path', GODOT_PROJECT, godot_scene]

    # Keep the window unoccluded: macOS throttles covered windows' rendering,
    # which freezes the camera stream while physics keeps running — the bot
    # then drives blind on a stale frame (looks like random divergence).
    godot_cmd.append('--always-on-top')

    # Per-bot args for the convoy scene (two bots) come in via user_args; the
    # single-bot tasks build their one set from the explicit port params.
    extra_args = list(user_args) if user_args else []
    if not extra_args:
        if camera_port is not None:
            extra_args.append(f'--camera-port={camera_port}')
        if wheel_port_hint is not None:
            extra_args.append(f'--wheel-port={wheel_port_hint}')
        if port_file_path is not None:
            extra_args.append(f'--port-file={port_file_path}')
    if extra_args:
        godot_cmd.append('--')
        godot_cmd.extend(extra_args)

    try:
        if debug:
            godot_process = subprocess.Popen(godot_cmd, cwd=GODOT_PROJECT)
        else:
            godot_process = subprocess.Popen(godot_cmd,
                                              stdout=subprocess.DEVNULL,
                                              stderr=subprocess.DEVNULL,
                                              cwd=GODOT_PROJECT)
        print(f"✅ Godot started (PID: {godot_process.pid})")
        return True
    except Exception as e:
        print(f"❌ ERROR: Failed to launch Godot: {e}")
        return False


def stop_godot():
    global godot_process
    if godot_process:
        print("Stopping Godot...")
        try:
            godot_process.terminate()
            godot_process.wait(timeout=3)
        except Exception:
            godot_process.kill()
        godot_process = None


def _kill_stale_sim_processes():
    """Clear leftovers from a previous sim run. A zombie Godot or virtual
    server keeps the old camera/wheel sockets alive and hijacks the new
    run's connections — the fresh sim then 'freezes' waiting for frames."""
    if platform.system() == 'Windows':
        return
    import signal as _signal
    me = os.getpid()
    killed = 0
    for pattern in ('launch.py --sim', 'GodotSimulation/ducky-bot', 'virtual_server.py --port'):
        try:
            out = subprocess.run(['pgrep', '-f', pattern],
                                 capture_output=True, text=True).stdout
            for pid_s in out.split():
                pid = int(pid_s)
                if pid in (me, os.getppid()):
                    continue
                try:
                    os.kill(pid, _signal.SIGTERM)
                    killed += 1
                except (ProcessLookupError, PermissionError):
                    pass
        except Exception:
            pass
    if killed:
        print(f"  Cleaned up {killed} stale sim process(es) from a previous run")
        import time as _time
        _time.sleep(0.8)  # let their sockets close before binding ours


def run_in_simulation(args):
    print("\n" + "=" * 60)
    print("RUN IN SIMULATION")
    print("=" * 60)

    if not args.task:
        print("❌ ERROR: Task name required")
        print("   Usage: python launch.py --sim --task <task>")
        return 1

    task_name = args.task
    print(f"Task: {task_name}\n")

    godot_scene = GODOT_SCENES.get(task_name)
    if not godot_scene:
        print(f"❌ ERROR: No Godot scene configured for task '{task_name}'")
        print(f"   Available tasks: {', '.join(GODOT_SCENES.keys())}")
        return 1

    virtual_server_path = os.path.join(PROJECT_ROOT, 'servers', task_name, 'virtual_server.py')
    if not os.path.exists(virtual_server_path):
        print(f"❌ ERROR: No virtual server found at servers/{task_name}/virtual_server.py")
        return 1

    _kill_stale_sim_processes()

    print("[0/3] Finding available ports...")
    used_ports = set()
    camera_port = find_available_port(5001, exclude=used_ports)
    used_ports.add(camera_port)
    print(f"  Camera port: {camera_port}")
    wheel_port_hint = find_available_port(5002, exclude=used_ports)
    used_ports.add(wheel_port_hint)
    print(f"  Wheel port hint: {wheel_port_hint}")

    tmp_dir = tempfile.mkdtemp(prefix="ducky_")
    port_file_path = os.path.join(tmp_dir, "ports.json")

    print("\n[1/3] Launching Godot...")
    if not launch_godot(args.godot_path, args.debug,
                        camera_port=camera_port,
                        wheel_port_hint=wheel_port_hint,
                        port_file_path=port_file_path,
                        scene=godot_scene):
        return 1

    godot_init_timeout = 60 if platform.system() == 'Darwin' else 15
    print(f"Waiting for Godot to initialize (timeout: {godot_init_timeout}s)...")
    try:
        port_data = wait_for_port_file(port_file_path, timeout=godot_init_timeout)
        wheel_port = port_data.get("wheel_port", wheel_port_hint)
        print(f"  Godot wheel port: {wheel_port}")
    except TimeoutError:
        if godot_process and godot_process.poll() is not None:
            print("❌ ERROR: Godot exited unexpectedly!")
            return 1
        print("  Port file not found, assuming hint port is correct")
        wheel_port = wheel_port_hint

    if godot_process and godot_process.poll() is not None:
        print("❌ ERROR: Godot exited unexpectedly!")
        return 1

    print(f"\n[2/3] Starting {task_name} virtual server...")
    server = importlib.import_module(f'servers.{task_name}.virtual_server')

    old_argv = sys.argv.copy()
    sys.argv = [f'{task_name}_virtual_server.py',
                '--port', str(args.port),
                '--frame-port', str(camera_port),
                '--wheel-port', str(wheel_port)]
    try:
        server.main()
    finally:
        sys.argv = old_argv
        stop_godot()
        try:
            os.remove(port_file_path)
            os.rmdir(tmp_dir)
        except OSError:
            pass

    return 0


def run_convoy(args):
    """Run the lead + follower convoy in ONE Godot world (the convoy.tscn scene
    with two real agent-driven bots) plus BOTH virtual servers, each on its own
    web/camera/wheel ports. Reuses the unchanged project_lead/project_follow
    servers; they run as separate subprocesses so their module-level Flask app /
    camera / wheels globals stay isolated."""
    import time

    print("\n" + "=" * 60)
    print("RUN CONVOY SIMULATION (lead + follower, one Godot world)")
    print("=" * 60)

    convoy_scene = GODOT_SCENES.get('convoy')
    if not convoy_scene:
        print("❌ ERROR: No 'convoy' scene configured in GODOT_SCENES")
        return 1

    lead_server   = os.path.join(PROJECT_ROOT, 'servers', 'project_lead', 'virtual_server.py')
    follow_server = os.path.join(PROJECT_ROOT, 'servers', 'project_follow', 'virtual_server.py')
    for path, role in ((lead_server, 'project_lead'), (follow_server, 'project_follow')):
        if not os.path.exists(path):
            print(f"❌ ERROR: No virtual server found at servers/{role}/virtual_server.py")
            return 1

    _kill_stale_sim_processes()

    print("[0/4] Finding available ports...")
    used = set()
    # Disjoint bands: the wheel server auto-probes base..base+19, so keep the
    # lead (5002) and follow (5102) hint bases >=100 apart to avoid overlap.
    lead_cam   = find_available_port(5001, exclude=used); used.add(lead_cam)
    lead_whl   = find_available_port(5002, exclude=used); used.add(lead_whl)
    follow_cam = find_available_port(5101, exclude=used); used.add(follow_cam)
    follow_whl = find_available_port(5102, exclude=used); used.add(follow_whl)
    lead_web   = find_available_port(args.port,      exclude=used); used.add(lead_web)
    follow_web = find_available_port(args.port + 10, exclude=used); used.add(follow_web)
    print(f"  lead   camera={lead_cam} wheel_hint={lead_whl} web={lead_web}")
    print(f"  follow camera={follow_cam} wheel_hint={follow_whl} web={follow_web}")

    tmp_dir   = tempfile.mkdtemp(prefix="ducky_convoy_")
    lead_pf   = os.path.join(tmp_dir, "lead_ports.json")
    follow_pf = os.path.join(tmp_dir, "follow_ports.json")

    user_args = [
        f'--lead-camera-port={lead_cam}',     f'--lead-wheel-port={lead_whl}',     f'--lead-port-file={lead_pf}',
        f'--follow-camera-port={follow_cam}', f'--follow-wheel-port={follow_whl}', f'--follow-port-file={follow_pf}',
    ]

    print("\n[1/4] Launching Godot (convoy scene)...")
    if not launch_godot(args.godot_path, args.debug, scene=convoy_scene, user_args=user_args):
        return 1

    godot_init_timeout = 60 if platform.system() == 'Darwin' else 15
    print(f"Waiting for Godot to initialize (timeout: {godot_init_timeout}s)...")
    lead_wheel, follow_wheel = lead_whl, follow_whl
    try:
        lead_wheel = wait_for_port_file(lead_pf, timeout=godot_init_timeout).get("wheel_port", lead_whl)
        follow_wheel = wait_for_port_file(follow_pf, timeout=godot_init_timeout).get("wheel_port", follow_whl)
        print(f"  Godot wheel ports: lead={lead_wheel} follow={follow_wheel}")
    except TimeoutError:
        if godot_process and godot_process.poll() is not None:
            print("❌ ERROR: Godot exited unexpectedly!")
            stop_godot()
            return 1
        print("  Port file(s) not found; assuming hint ports are correct")

    if godot_process and godot_process.poll() is not None:
        print("❌ ERROR: Godot exited unexpectedly!")
        return 1

    # Start both servers together. The follower has no "wait" state — with no
    # leader in view it lane-follows forward on its own — so it must NOT be
    # started before the lead (it would drive ahead of the lead's spawn and
    # never get the marker in front of it). Near-simultaneous start keeps the
    # follower trailing the lead at roughly the spawn gap.
    print("\n[2/4] Starting lead server...")
    lead_proc = subprocess.Popen(
        [sys.executable, lead_server, '--port', str(lead_web),
         '--frame-port', str(lead_cam), '--wheel-port', str(lead_wheel)],
        cwd=PROJECT_ROOT)

    print("[3/4] Starting follower server...")
    follow_proc = subprocess.Popen(
        [sys.executable, follow_server, '--port', str(follow_web),
         '--frame-port', str(follow_cam), '--wheel-port', str(follow_wheel)],
        cwd=PROJECT_ROOT)

    print("\n[4/4] Convoy running:")
    print(f"   Lead   UI: http://localhost:{lead_web}")
    print(f"   Follow UI: http://localhost:{follow_web}")
    print("   Ctrl+C to stop.\n")

    try:
        while True:
            if godot_process and godot_process.poll() is not None:
                print("\nGodot exited; shutting down convoy.")
                break
            if lead_proc.poll() is not None:
                print("\nLead server exited; shutting down convoy.")
                break
            if follow_proc.poll() is not None:
                print("\nFollower server exited; shutting down convoy.")
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nShutting down convoy...")
    finally:
        for proc in (lead_proc, follow_proc):
            if proc and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=3)
                except Exception:
                    proc.kill()
        stop_godot()
        try:
            os.remove(lead_pf)
            os.remove(follow_pf)
            os.rmdir(tmp_dir)
        except OSError:
            pass

    return 0


def _bot_host(target):
    return target if target.replace('.', '').isdigit() else f"{target}.local"


def package_task(task_name):
    print(f"Packaging task: {task_name}")
    task_packages_dir = os.path.join(PROJECT_ROOT, 'tasks', task_name, 'packages')
    config_dir = os.path.join(PROJECT_ROOT, 'config')

    if not os.path.exists(task_packages_dir):
        print(f"Error: Task packages directory not found: {task_packages_dir}")
        return None

    def no_pycache(tarinfo):
        if '__pycache__' in tarinfo.name or tarinfo.name.endswith('.pyc'):
            return None
        return tarinfo

    task_models_dir = os.path.join(PROJECT_ROOT, 'tasks', task_name, 'models')
    task_server_dir = os.path.join(PROJECT_ROOT, 'servers', task_name)

    # Define dependencies for tasks that import from other tasks
    task_dependencies = {
        # The convoy roles share tasks/project/packages as a library and reuse
        # the lane follower. Neither needs object_detection (no YOLO).
        'project_lead':   ['project', 'visual_lane_servoing'],
        'project_follow': ['project', 'visual_lane_servoing'],
    }

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz') as tar:
        print(f"   Adding packages: tasks/{task_name}/packages/")
        tar.add(task_packages_dir, arcname=f'tasks/{task_name}/packages', filter=no_pycache)

        # Add dependency task packages
        if task_name in task_dependencies:
            for dep_task in task_dependencies[task_name]:
                dep_packages_dir = os.path.join(PROJECT_ROOT, 'tasks', dep_task, 'packages')
                if os.path.exists(dep_packages_dir):
                    print(f"   Adding dependency: tasks/{dep_task}/packages/")
                    tar.add(dep_packages_dir, arcname=f'tasks/{dep_task}/packages', filter=no_pycache)
                dep_models_dir = os.path.join(PROJECT_ROOT, 'tasks', dep_task, 'models')
                if os.path.exists(dep_models_dir):
                    print(f"   Adding dependency models: tasks/{dep_task}/models/")
                    tar.add(dep_models_dir, arcname=f'tasks/{dep_task}/models', filter=no_pycache)

        if os.path.exists(config_dir):
            print(f"   Adding configs: config/")
            tar.add(config_dir, arcname='config', filter=no_pycache)
        if os.path.exists(task_models_dir):
            print(f"   Adding models: tasks/{task_name}/models/")
            tar.add(task_models_dir, arcname=f'tasks/{task_name}/models', filter=no_pycache)
        if os.path.exists(task_server_dir):
            print(f"   Adding server: servers/{task_name}/")
            tar.add(task_server_dir, arcname=f'servers/{task_name}', filter=no_pycache)

        # Shared modules the task servers import (web template, helpers, ports).
        for shared in ('servers/__init__.py', 'servers/common.py', 'servers/sim_map.py',
                       'servers/templates', 'launcher'):
            shared_path = os.path.join(PROJECT_ROOT, shared)
            if os.path.exists(shared_path):
                tar.add(shared_path, arcname=shared, filter=no_pycache)

    buf.seek(0)
    print("Package created!")
    return buf


def transfer_to_bot(bot_target, package_data, task_name, port):
    host = _bot_host(bot_target)
    print(f"Connecting to {host}:{port}")
    try:
        response = requests.post(f"http://{host}:{port}/deploy",
                                 files={'package': ('task.tar.gz', package_data, 'application/gzip')},
                                 data={'task': task_name},
                                 timeout=30)
        if response.status_code == 200:
            print("Transfer successful!")
            print(f"   {response.json().get('message', 'Deployed')}")
            return True
        print(f"Transfer failed: {response.status_code}\n   {response.text}")
        return False
    except requests.exceptions.ConnectionError:
        print(f"Error: Could not connect to {host}:{port}")
        print(f"Bot is powered on and connected to network")
        print(f"Dashboard is running on the bot")
        print(f"Host/IP '{host}' is correct")
        return False
    except requests.exceptions.Timeout:
        print("Error: Connection timeout")
        return False
    except Exception as e:
        print(f"Error: {e}")
        return False


def start_task_on_bot(bot_target, task_name, task_port, deploy_port, debug=False):
    host = _bot_host(bot_target)
    print(f"Starting task '{task_name}' on {host}")
    try:
        response = requests.post(f"http://{host}:{deploy_port}/start",
                                 json={'task': task_name, 'port': task_port, 'debug': debug},
                                 timeout=10)
        if response.status_code == 200:
            result = response.json()
            print(f"Task started!")
            print(f"   PID: {result.get('pid')}")
            print(f"   Port: {result.get('port')}")
            print(f"   Web UI: http://{host}:{result.get('port')}")
            return True
        print(f"Failed to start task: {response.status_code}\n   {response.text}")
        return False
    except Exception as e:
        print(f"Error: {e}")
        return False


def stop_task_on_bot(bot_target, deploy_port):
    host = _bot_host(bot_target)
    print(f"Stopping task on {host}")
    try:
        response = requests.post(f"http://{host}:{deploy_port}/stop", timeout=10)
        if response.status_code == 200:
            print(f"{response.json().get('message')}")
            return True
        print(f"Failed to stop task: {response.status_code}")
        return False
    except Exception as e:
        print(f"Error: {e}")
        return False


def run_on_bot(args):
    print()
    print("Run On Hardware")
    print()

    if not args.task:
        print("Error: --task required")
        return 1
    if not args.bot and not args.host:
        print("Error: --bot or --host required")
        return 1

    task_name = args.task
    bot_target = args.bot or args.host
    deploy_port = args.deploy_port
    task_port = args.port

    print(f"Task: {task_name}\nBot: {bot_target}\n")

    print("[0/3] Stopping any running task...")
    stop_task_on_bot(bot_target, deploy_port)

    print("\n[1/3] Building and deploying...")
    package = package_task(task_name)
    if not package:
        return 1
    if not transfer_to_bot(bot_target, package, task_name, deploy_port):
        return 1
    print("Deployment complete!")

    print("\n[2/3] Starting task...")
    if start_task_on_bot(bot_target, task_name, task_port, deploy_port, debug=args.debug):
        host = _bot_host(bot_target)
        print(f"\nTask '{task_name}' is running!")
        print(f"   Web UI: http://{host}:{task_port}")
        return 0
    return 1


def stop_on_bot(args):
    print()
    print("Stop Task!!")
    print()

    bot_target = args.bot or args.host
    if not bot_target:
        print("Error: --bot or --host required")
        return 1

    return 0 if stop_task_on_bot(bot_target, args.deploy_port) else 1


def main():
    parser = argparse.ArgumentParser(
        description="DuckieTown Task Launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python launch.py --sim --task braitenberg
  python launch.py --sim --task braitenberg --debug
  python launch.py --convoy
  python launch.py --convoy --debug
  python launch.py --run --bot kvati --task braitenberg
  python launch.py --run --host 192.168.1.100 --task introduction
  python launch.py --stop --bot kvati
        """
    )

    parser.add_argument("--sim",  action="store_true", help="Run in simulation")
    parser.add_argument("--convoy", action="store_true",
                        help="Run the lead+follower convoy in one Godot world (two servers)")
    parser.add_argument("--run",  action="store_true", help="Deploy and run on hardware")
    parser.add_argument("--stop", action="store_true", help="Stop task on hardware")

    parser.add_argument("--task", type=str, default=None)
    parser.add_argument("--bot",  type=str, default=None, help="Bot hostname (connects to <name>.local)")
    parser.add_argument("--host", type=str, default=None, help="Bot IP address")
    parser.add_argument("--deploy-port", type=int, default=8000)
    parser.add_argument("--port", type=int, default=5000, help="Task web server port")
    parser.add_argument("--godot-path", type=str, default=None)
    parser.add_argument("--debug", action="store_true", help="Show Godot console output")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        stream=sys.stdout,
        format='%(levelname)s %(message)s',
    )

    if args.run:
        return run_on_bot(args)
    elif args.stop:
        return stop_on_bot(args)
    elif args.convoy:
        return run_convoy(args)
    elif args.sim:
        return run_in_simulation(args)
    else:
        parser.print_help()
        print("\nError: Please specify a mode: --sim, --convoy, --run, or --stop")
        return 1


if __name__ == "__main__":
    sys.exit(main())
