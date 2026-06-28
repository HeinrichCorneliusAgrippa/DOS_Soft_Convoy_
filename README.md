# DuckieTown Rewritten

A robotics education platform for the Duckiebot DB21J. Students complete programming tasks that run either in a Godot 4.6 simulation or on the real robot. Each task has a set of notebooks to guide the theory, a package of Python files to implement, and a live web interface to observe and tune the robot's behavior.

## IDE Setup

### VSCode

Install these extensions (search by name in the Extensions panel):

| Extension | Why |
|-----------|-----|
| **Python** (`ms-python.python`) | Python language support, linting, debugging |
| **Pylance** (`ms-python.vscode-pylance`) | Fast type checking and autocomplete (installed automatically with Python) |
| **Jupyter** (`ms-toolsai.jupyter`) | Run `.ipynb` notebooks directly in VSCode |

### PyCharm

PyCharm works out of the box — no extensions needed.


**Notebooks:** PyCharm Professional runs Jupyter notebooks natively. PyCharm Community does not — use the terminal instead:
```bash
jupyter notebook
```

### 1. Create a virtual environment

## Quick Setup

**Linux / macOS**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

**Windows**
```bash
python -m venv .venv
.venv\Scripts\activate
```

### 2. Install dependencies

**Simulation (your laptop/desktop):**
```bash
pip install -r requirements.txt
```

## Robot Dashboard

Every robot runs a dashboard server on boot, accessible at:

```
http://<BOTNAME>.local:8000
```

For example, if your bot is named `kvati`:

```
http://kvati.local:8000
```

The dashboard lets you:
- See the robot's live camera feed
- Open and stop tasks
- View task logs in real time
- Monitor battery voltage

The dashboard must be running before you can deploy a task with `launch.py --run`. It starts automatically on boot — no action needed.


## Running a task

All tasks are launched through `launch.py` from the project root.

### Simulation run
---
```bash
python launch.py --sim --task <TASK NAME> 
```

Godot is downloaded automatically on the first run. A URL is printed in the terminal — open it in a browser to see the live camera feed and controls.

### Real robot run
---
```bash
# By bot hostname (.local mDNS)
python launch.py --run --bot <bot_name> 
                 --task <TASK NAME>

# By IP address
python launch.py --run --host 192.168.1.100 
                 --task <TASK NAME>
```

This packages the task, transfers it to the robot over HTTP, and starts the server. The terminal prints the web interface URL when ready.

### Stop a task on the robot

---
```bash
python launch.py --stop --bot <bot_name>
```


## All flags


```
python launch.py --help

  --sim                 Run in simulation (Godot)
  --run                 Deploy and run on real bot
  --stop                Stop task running on bot

  --task <TASK NAME>    Name of task you run 

  --bot <BOT NAME>      name of bot on which you run
  
  --host HOST           Bot IP on which you run
  
  --debug               Shows debug level of info
```

## How the API works

Each task server is a Flask web application. The browser-based interface communicates with it entirely through HTTP requests, so the robot itself has no display requirement.

**Video stream**

The camera feed is a standard MJPEG stream served at `/video`. The browser displays it as a live image using a standard `img` tag.

**Reading state**

The interface polls `/status` every few hundred milliseconds with a GET request. The server responds with a JSON object containing the current pose, frame count, active maneuver, or whatever is relevant for the task. The page updates the displayed values from this response.

**Changing configuration**

Sliders and input fields send their values to endpoints like `/update_config` or `/update_hsv` as POST requests with a JSON body. The server updates its in-memory config object immediately, so changes take effect on the next processing cycle without restarting anything. The new values are also written back to the relevant YAML file in `config/` so they persist across restarts.

