"""Shared web UI for the convoy (project_lead / project_follow) sim servers.

get_template(task) returns a Jinja template string for render_template_string.
The page expects one Jinja variable, map_json: the parsed map from
servers.sim_map (or null), used by the client-side top-down track renderer.

The server behind it must expose:
  GET  /video         MJPEG debug montage
  GET  /status        {agent:{...debug...}, agent_running, pose|null, game,
                       route (lead) / leader_cfg (follow)}
  GET  /sample?fx&fy  click-to-sample HSV (same contract as the real servers)
  POST /agent/start, /agent/stop, /reset
"""
from servers.templates.base import render_template

_EXTRA_CSS = '''
.state-badge {
    font-size: 12px; font-weight: 600; padding: 3px 10px; border-radius: 12px;
    background: var(--bg-sidebar); border: 1px solid var(--border-color);
    color: var(--text-secondary);
}
.kv {
    display: flex; justify-content: space-between; align-items: center;
    padding: 6px 0; border-bottom: 1px solid var(--border-color);
    font-size: 13px; color: var(--text-secondary);
}
.kv:last-child { border-bottom: none; }
.kv b { color: var(--text-primary); font-weight: 500; font-family: ui-monospace, monospace; }

.route-row { display: flex; flex-wrap: wrap; gap: 6px; padding: 8px 0; }
.route-chip {
    min-width: 34px; text-align: center; padding: 4px 8px; border-radius: 5px;
    font-size: 13px; font-weight: 600; background: var(--bg-sidebar);
    border: 1px solid var(--border-color); color: var(--text-muted);
}
.route-chip.done    { color: var(--accent-green); border-color: var(--accent-green); opacity: 0.55; }
.route-chip.current { color: #fff; background: var(--accent-blue); border-color: var(--accent-blue); }

.gauge-row { display: flex; align-items: center; gap: 8px; padding: 8px 0; }
.gauge-label { font-size: 10px; color: var(--text-muted); text-transform: uppercase; }
.gauge {
    flex: 1; position: relative; height: 14px; border-radius: 7px;
    background: var(--bg-sidebar); border: 1px solid var(--border-color); overflow: hidden;
}
.gauge-fill {
    position: absolute; left: 0; top: 0; bottom: 0; width: 0%;
    background: linear-gradient(90deg, var(--accent-green), var(--accent-orange), var(--accent-red));
    transition: width 0.2s;
}
.gauge-mark { position: absolute; top: 0; bottom: 0; width: 2px; background: rgba(230,237,243,0.5); }

#map-canvas { display: block; border-radius: 4px; }
.map-note { font-size: 11px; color: var(--text-muted); padding-top: 6px; min-height: 16px; }
.hsv-hint { font-size: 12px; color: var(--text-secondary); margin-bottom: 8px; }
.hsv-out { font-family: ui-monospace, monospace; font-size: 14px; min-height: 20px; }

/* manual drive */
.switch-row {
    display: flex; align-items: center; justify-content: space-between;
    padding: 4px 0 8px; font-size: 13px; color: var(--text-secondary);
}
.switch-row input { display: none; }
.switch {
    width: 42px; height: 22px; border-radius: 11px; cursor: pointer;
    background: var(--bg-sidebar); border: 1px solid var(--border-color);
    position: relative; transition: background .15s, border-color .15s;
}
.switch::after {
    content: ''; position: absolute; top: 2px; left: 2px; width: 16px; height: 16px;
    border-radius: 50%; background: var(--text-muted); transition: transform .15s, background .15s;
}
.switch-row input:checked + .switch { background: var(--accent-blue); border-color: var(--accent-blue); }
.switch-row input:checked + .switch::after { transform: translateX(20px); background: #fff; }
.pad-hint { font-size: 11px; color: var(--text-muted); margin-bottom: 8px; }
.pad-hint b { color: var(--text-secondary); font-family: ui-monospace, monospace; }
.pad-grid {
    display: grid; grid-template-columns: repeat(3, 1fr); grid-template-rows: repeat(3, 42px);
    gap: 6px; max-width: 174px; margin: 0 auto;
    grid-template-areas: ". up ." "left mid right" ". down .";
}
.padbtn {
    border: 1px solid var(--border-color); background: var(--bg-sidebar);
    color: var(--text-primary); border-radius: 6px; font-size: 15px;
    cursor: pointer; user-select: none; touch-action: none; transition: background .1s;
}
.padbtn:hover { background: var(--border-color); }
.padbtn.active { background: var(--accent-blue); border-color: var(--accent-blue); color: #fff; }
.padbtn.stop { color: var(--accent-red); }
.padbtn.stop.active { background: var(--accent-red); border-color: var(--accent-red); color: #fff; }
'''


def _state_panel(task):
    if task == 'lead':
        return '''
            <div class="route-row" id="route-row"></div>
            <div class="kv"><span>Stop line</span><b id="kv-redline">&mdash;</b></div>
            <div class="kv"><span>AprilTags</span><b id="kv-tags">&mdash;</b></div>
            <div class="kv"><span>Sim</span><b id="kv-game">&mdash;</b></div>'''
    return '''
            <div class="kv"><span>Leader</span><b id="kv-leader">not seen</b></div>
            <div class="gauge-row"><span class="gauge-label">far</span>
                <div class="gauge"><div class="gauge-fill" id="span-fill"></div>
                    <div class="gauge-mark" id="mark-safe"></div>
                    <div class="gauge-mark" id="mark-stop"></div></div>
                <span class="gauge-label">close</span></div>
            <div class="kv"><span>Lateral</span><b id="kv-lateral">&mdash;</b></div>
            <div class="kv"><span>Sim</span><b id="kv-game">&mdash;</b></div>'''


def _manual_card(task):
    """Lead-only: switch between autonomous and manual drive + a D-pad. The
    server exposes /mode (toggle) and /drive (linear/angular); a deadman
    watchdog there stops the bot if commands stop arriving."""
    if task != 'lead':
        return ''
    return '''
            <div class="card" id="manual-card">
                <div class="card-header">Drive control <span class="state-badge" id="mode-badge">autonomous</span></div>
                <label class="switch-row">
                    <span>Manual drive</span>
                    <input type="checkbox" id="mode-toggle" onchange="setMode(this.checked)">
                    <span class="switch"></span>
                </label>
                <div id="manual-pad" hidden>
                    <div class="pad-hint">Hold a button or use <b>W A S D</b> / arrow keys &mdash; release to stop.</div>
                    <div class="pad-grid">
                        <button class="padbtn" data-dir="fwd"   style="grid-area:up">&#9650;</button>
                        <button class="padbtn" data-dir="left"  style="grid-area:left">&#9664;</button>
                        <button class="padbtn stop" data-dir="stop" style="grid-area:mid">&#9632;</button>
                        <button class="padbtn" data-dir="right" style="grid-area:right">&#9654;</button>
                        <button class="padbtn" data-dir="back"  style="grid-area:down">&#9660;</button>
                    </div>
                    <div class="status" id="manual-status"></div>
                </div>
            </div>'''


# Live HSV knobs: (slider id, label, max). All min 0, step 1; ids map to the
# lane_servoing_hsv_config keys in _JS_COMMON's HSV_KNOBS table.
_HSV_SLIDERS = (
    ('hsv-ylh', 'Yellow H min', 60),
    ('hsv-yuh', 'Yellow H max', 90),
    ('hsv-yls', 'Yellow S min', 255),
    ('hsv-ylv', 'Yellow V min', 255),
    ('hsv-wus', 'White S max', 150),
    ('hsv-wlv', 'White V min', 255),
)


def _hsv_sliders():
    return ''.join(f'''
                <div class="slider-group">
                    <div class="slider-label"><span>{label}</span><span id="{sid}-val">&mdash;</span></div>
                    <div class="slider-controls">
                        <input type="range" class="slider" id="{sid}" min="0" max="{hi}" step="1">
                    </div>
                </div>''' for sid, label, hi in _HSV_SLIDERS)


def _content(task, sim=True):
    run_card = ('''
            <div class="card">
                <div class="card-header">Simulation</div>
                <button class="button" id="btn-agent" onclick="toggleAgent()">Pause agent</button>
                <button class="button danger" onclick="resetSim()">Reset simulation</button>
                <div class="status" id="sim-status"></div>
            </div>''' if sim else '''
            <div class="card">
                <div class="card-header">Robot</div>
                <button class="button" id="btn-agent" onclick="toggleAgent()">Pause agent</button>
                <button class="button danger" onclick="resetSim()">Restart agent</button>
                <div class="status" id="sim-status"></div>
            </div>''')
    manual_card = _manual_card(task)
    return f'''
    <div class="container">
        <div class="video-section">
            <img id="cam" class="stream" src="/video" alt="camera"
                 title="click the top-left camera panel to sample H/S/V">
        </div>
        <div class="controls-section">
            <div class="card">
                <div class="card-header">Agent <span class="state-badge" id="state-badge">&mdash;</span></div>
                <div class="stats-grid">
                    <div class="stat-box"><div class="stat-value" id="stat-speed">0.00</div><div class="stat-label">Base speed</div></div>
                    <div class="stat-box"><div class="stat-value" id="stat-steer">+0.00</div><div class="stat-label">Steering</div></div>
                    <div class="stat-box"><div class="stat-value" id="stat-wheels">&mdash;</div><div class="stat-label">L / R wheels</div></div>
                    <div class="stat-box"><div class="stat-value" id="stat-fps">0</div><div class="stat-label">Agent FPS</div></div>
                </div>{_state_panel(task)}
            </div>{manual_card}
            <div class="card">
                <div class="card-header">Track map <span class="state-badge" id="pose-source">no pose</span></div>
                <canvas id="map-canvas"></canvas>
                <div class="map-note" id="map-note"></div>
            </div>{run_card}
            <div class="card">
                <div class="card-header">Tuning <span class="state-badge">live</span></div>
                <div class="slider-group">
                    <div class="slider-label"><span>Cruise speed</span><span id="tune-speed-val">&mdash;</span></div>
                    <div class="slider-controls">
                        <input type="range" class="slider" id="tune-speed" min="0.05" max="0.6" step="0.01">
                    </div>
                </div>
                <div class="slider-group">
                    <div class="slider-label"><span>Lane Kp</span><span id="tune-kp-val">&mdash;</span></div>
                    <div class="slider-controls">
                        <input type="range" class="slider" id="tune-kp" min="0" max="1" step="0.01">
                    </div>
                </div>
                <div class="slider-group">
                    <div class="slider-label"><span>Lane Kd</span><span id="tune-kd-val">&mdash;</span></div>
                    <div class="slider-controls">
                        <input type="range" class="slider" id="tune-kd" min="0" max="2" step="0.01">
                    </div>
                </div>
                <div class="status" id="tune-status"></div>
            </div>
            <div class="card">
                <div class="card-header">Lane HSV <span class="state-badge">live</span></div>
                <div class="hsv-hint">Bounds apply to the running detector instantly &mdash; watch
                the YELLOW / WHITE mask panels while dragging.</div>
                {_hsv_sliders()}
                <div class="status" id="hsv-status"></div>
            </div>
            <div class="card">
                <div class="card-header">HSV sampler</div>
                <div class="hsv-hint">Click the camera (top-left) panel of the video to read the
                H/S/V the detectors see &mdash; then set the bounds above around it.</div>
                <div class="hsv-out" id="hsv-out">&mdash;</div>
            </div>
        </div>
    </div>'''


_JS_COMMON = '''
const MAP = {{ map_json|safe }};
const TASK = '__TASK__';
const SIM = __SIM__;

const STATE_COLORS = {
    LANE_FOLLOW: 'var(--accent-green)', FOLLOW: 'var(--accent-green)',
    STOP_AT_SIGN: 'var(--accent-red)', CLOSE_STOP: 'var(--accent-red)',
    ROUTE_DONE: 'var(--accent-purple)',
    SLOW_ZONE: 'var(--accent-orange)', SLOW_AFTER_TURN: 'var(--accent-orange)',
    REACQUIRE: 'var(--accent-orange)', PURSUIT_TURN: 'var(--accent-orange)',
    CURVE: 'var(--accent-orange)',
    TURN_LEFT: 'var(--accent-blue)', TURN_RIGHT: 'var(--accent-blue)',
    CROSS_STRAIGHT: 'var(--accent-blue)',
    WAIT_LEAD: 'var(--text-muted)', HOLD: 'var(--text-muted)'
};
const ROUTE_GLYPHS = { straight: '\\u2191', left: '\\u2190', right: '\\u2192', stop: '\\u25A0' };

let agentRunning = true;
let manualMode = false;
let leaderCfg = null;

function setText(id, text) { document.getElementById(id).textContent = text; }

function updateAgentPanel(d) {
    const a = d.agent || {};
    if (TASK === 'lead' && d.manual_mode !== undefined) applyMode(d.manual_mode);
    const badge = document.getElementById('state-badge');
    const state = a.state || 'INIT';
    if (manualMode) {
        badge.textContent = 'MANUAL'; badge.style.color = 'var(--accent-blue)';
    } else {
        badge.textContent = d.agent_running ? state : 'PAUSED';
        badge.style.color = d.agent_running ? (STATE_COLORS[state] || 'var(--text-secondary)') : 'var(--text-muted)';
    }
    setText('stat-speed', (a.base_speed || 0).toFixed(2));
    setText('stat-steer', ((a.steering || 0) >= 0 ? '+' : '') + (a.steering || 0).toFixed(2));
    setText('stat-wheels', (a.left_speed || 0).toFixed(2) + ' / ' + (a.right_speed || 0).toFixed(2));
    setText('stat-fps', (a.fps || 0).toFixed(1));

    const g = d.game || {};
    const gameEl = document.getElementById('kv-game');
    if (gameEl) {
        gameEl.parentElement.style.display = Object.keys(g).length ? '' : 'none';
        if (g.game_over) {
            gameEl.textContent = 'GAME OVER' + (g.collision_duck ? ' (hit ' + g.collision_duck + ')' : '');
            gameEl.style.color = 'var(--accent-red)';
        } else {
            gameEl.textContent = (g.total_distance || 0).toFixed(1) + ' m driven';
            gameEl.style.color = '';
        }
    }

    agentRunning = !!d.agent_running;
    document.getElementById('btn-agent').textContent =
        manualMode ? 'Manual mode' : (agentRunning ? 'Pause agent' : 'Start agent');

    if (TASK === 'lead') updateLeadPanel(d, a); else updateFollowPanel(d, a);
}

function updateLeadPanel(d, a) {
    const row = document.getElementById('route-row');
    const idx = a.route_idx || 0;
    row.innerHTML = '';
    if (d.route_mode === 'auto') {
        const chip = document.createElement('span');
        chip.className = 'route-chip current';
        chip.textContent = 'AUTO \\u00b7 ' + idx +
            (a.route_step ? ' \\u00b7 last ' + (ROUTE_GLYPHS[a.route_step] || a.route_step) : '');
        chip.title = 'turns chosen from the map; ' + idx + ' intersections so far';
        row.appendChild(chip);
    } else {
        (d.route || []).forEach((step, i) => {
            const chip = document.createElement('span');
            chip.className = 'route-chip' + (i < idx ? ' done' : (i === idx ? ' current' : ''));
            chip.textContent = ROUTE_GLYPHS[step] || step;
            chip.title = step;
            row.appendChild(chip);
        });
    }
    const rl = a.red_line;
    setText('kv-redline', rl ? ('width ' + rl[0].toFixed(2) + ' \\u00b7 near ' + rl[1].toFixed(2)) : 'not seen');
    const tags = a.apriltag_ids || [];
    setText('kv-tags', tags.length ? tags.join(', ') : 'none');
}

function updateFollowPanel(d, a) {
    leaderCfg = d.leader_cfg || leaderCfg;
    const span = a.led_pair_px;
    const el = document.getElementById('kv-leader');
    if (a.leader_source && span) {
        el.textContent = a.leader_source + ' \\u00b7 span ' + span.toFixed(0) + ' px';
        el.style.color = 'var(--accent-green)';
    } else {
        el.textContent = 'not seen';
        el.style.color = 'var(--text-muted)';
    }
    if (leaderCfg) {
        const max = leaderCfg.grid_stop_px * 1.2;
        document.getElementById('span-fill').style.width =
            Math.min(100, 100 * (span || 0) / max).toFixed(1) + '%';
        document.getElementById('mark-safe').style.left = (100 * leaderCfg.grid_safe_px / max) + '%';
        document.getElementById('mark-stop').style.left = (100 * leaderCfg.grid_stop_px / max) + '%';
    }
    const lat = a.grid_lateral;
    setText('kv-lateral', (lat === null || lat === undefined) ? '\\u2014'
        : ((lat >= 0 ? '+' : '') + lat.toFixed(2) + (a.grid_heading ? ' \\u00b7 hdg ' + a.grid_heading.toFixed(2) : '')));
}

async function pollStatus() {
    try {
        const d = await (await fetch('/status')).json();
        updateAgentPanel(d);
        onPose(d.pose, d.leader_pose);
    } catch (e) { /* server restarting; keep polling */ }
}
setInterval(pollStatus, 250);
pollStatus();

async function toggleAgent() {
    const r = await postJSON(agentRunning ? '/agent/stop' : '/agent/start', {});
    showStatus('sim-status', r.message || 'ok', r.status === 'ok' ? 'success' : 'error');
}

// --- live tuning knobs (cruise speed + lane PD gains) ------------------------
// Sliders auto-apply (debounced) to the running agent and persist to config.
const TUNE_IDS = { speed: 'tune-speed', kp: 'tune-kp', kd: 'tune-kd' };
let tuneTimer = null;
let tuneDirty = false;

function setKnob(id, v) {
    const el = document.getElementById(id);
    if (v == null) return;
    if (!tuneDirty && document.activeElement !== el) el.value = v;
    document.getElementById(id + '-val').textContent = Number(el.value).toFixed(2);
}

async function loadTuning() {
    try {
        const d = await (await fetch('/tuning')).json();
        setKnob('tune-speed', d.speed);
        setKnob('tune-kp', d.kp);
        setKnob('tune-kd', d.kd);
    } catch (e) { /* agent may not be live yet */ }
}
loadTuning();
setTimeout(loadTuning, 3000);  // again once the agent thread is up

async function applyTuning() {
    const num = id => {
        const v = parseFloat(document.getElementById(id).value);
        return isNaN(v) ? null : v;
    };
    const r = await postJSON('/tuning',
        { speed: num('tune-speed'), kp: num('tune-kp'), kd: num('tune-kd') });
    tuneDirty = false;
    showStatus('tune-status', r.message || 'applied', r.status === 'ok' ? 'success' : 'error');
}

for (const id of Object.values(TUNE_IDS)) {
    document.getElementById(id).addEventListener('input', function () {
        tuneDirty = true;
        document.getElementById(id + '-val').textContent = Number(this.value).toFixed(2);
        clearTimeout(tuneTimer);
        tuneTimer = setTimeout(applyTuning, 300);
    });
}
async function resetSim() {
    const r = await postJSON('/reset', {});
    clearTrail();
    showStatus('sim-status', r.message || 'simulation reset', r.status === 'ok' ? 'success' : 'error');
}

// --- live lane HSV knobs -----------------------------------------------------
// Same auto-apply pattern as the tuning card; the detector picks the bounds up
// on the next frame, so the mask panels are the live preview.
const HSV_KNOBS = {
    'hsv-ylh': 'yellow_lower_h', 'hsv-yuh': 'yellow_upper_h',
    'hsv-yls': 'yellow_lower_s', 'hsv-ylv': 'yellow_lower_v',
    'hsv-wus': 'white_upper_s',  'hsv-wlv': 'white_lower_v',
};
let hsvTimer = null;
let hsvDirty = false;

async function loadHsv() {
    try {
        const d = await (await fetch('/hsv')).json();
        for (const [id, key] of Object.entries(HSV_KNOBS)) {
            const el = document.getElementById(id);
            if (d[key] == null) continue;
            if (!hsvDirty && document.activeElement !== el) el.value = d[key];
            document.getElementById(id + '-val').textContent = el.value;
        }
    } catch (e) { /* server may not expose /hsv yet */ }
}
loadHsv();
setTimeout(loadHsv, 3000);

async function applyHsv() {
    const body = {};
    for (const [id, key] of Object.entries(HSV_KNOBS)) {
        const v = parseInt(document.getElementById(id).value, 10);
        if (!isNaN(v)) body[key] = v;
    }
    const r = await postJSON('/hsv', body);
    hsvDirty = false;
    showStatus('hsv-status', r.message || 'applied', r.status === 'ok' ? 'success' : 'error');
}

for (const id of Object.keys(HSV_KNOBS)) {
    document.getElementById(id).addEventListener('input', function () {
        hsvDirty = true;
        document.getElementById(id + '-val').textContent = this.value;
        clearTimeout(hsvTimer);
        hsvTimer = setTimeout(applyHsv, 300);
    });
}

// --- click-to-sample HSV (camera = top-left quarter of the montage) ---------
// The stream is object-fit:contain, so the displayed frame is letterboxed
// inside the element; map the click through the contained content rect.
const cam = document.getElementById('cam');
cam.addEventListener('click', async (e) => {
    const r = cam.getBoundingClientRect();
    const iw = cam.naturalWidth, ih = cam.naturalHeight;
    if (!iw || !ih) return;
    const sc = Math.min(r.width / iw, r.height / ih);
    const dw = iw * sc, dh = ih * sc;
    const fx = (e.clientX - r.left - (r.width - dw) / 2) / dw;
    const fy = (e.clientY - r.top - (r.height - dh) / 2) / dh;
    if (fx < 0 || fx > 1 || fy < 0 || fy > 1) return;  // letterbox bar
    const out = document.getElementById('hsv-out');
    try {
        const d = await (await fetch('/sample?fx=' + fx.toFixed(4) + '&fy=' + fy.toFixed(4))).json();
        if (d.hint)  { out.style.color = 'var(--accent-orange)'; out.textContent = d.hint; return; }
        if (d.error) { out.style.color = 'var(--accent-red)'; out.textContent = d.error; return; }
        out.style.color = 'var(--accent-green)';
        out.textContent = 'H ' + d.h + '  S ' + d.s + '  V ' + d.v + '  (px ' + d.px + ',' + d.py + ')';
    } catch (err) { out.style.color = 'var(--accent-red)'; out.textContent = 'sample failed'; }
});

// --- manual drive (lead only) ------------------------------------------------
// A switch toggles /mode; while manual, held keys/buttons stream a (linear,
// angular) vector to /drive at ~8 Hz. Releasing everything sends one zero. The
// server's deadman watchdog also stops the bot if this stream stalls.
const active = new Set();        // currently-held directions
let driveTimer = null;

function applyMode(manual) {
    manualMode = !!manual;
    const tgl = document.getElementById('mode-toggle');
    const pad = document.getElementById('manual-pad');
    const badge = document.getElementById('mode-badge');
    const agentBtn = document.getElementById('btn-agent');
    if (tgl && document.activeElement !== tgl) tgl.checked = manualMode;
    if (pad) pad.hidden = !manualMode;
    if (badge) badge.textContent = manualMode ? 'manual' : 'autonomous';
    if (agentBtn) { agentBtn.disabled = manualMode; agentBtn.style.opacity = manualMode ? '0.5' : ''; }
    if (!manualMode && active.size) { active.clear(); stopDriveLoop(); }
}

async function setMode(manual) {
    applyMode(manual);                                   // optimistic
    const r = await postJSON('/mode', { manual: !!manual });
    if (r && typeof r.manual === 'boolean') applyMode(r.manual);
    showStatus('manual-status', r.message || '', r.status === 'ok' ? 'success' : 'error');
}

function driveVector() {
    let linear = 0, angular = 0;
    if (active.has('fwd'))   linear  += 1;
    if (active.has('back'))  linear  -= 1;
    if (active.has('right')) angular += 1;
    if (active.has('left'))  angular -= 1;
    return { linear, angular };
}

async function sendDrive() {
    if (!manualMode) return;
    try { await postJSON('/drive', driveVector()); } catch (e) { /* keep trying */ }
}

function startDriveLoop() {
    if (driveTimer || !manualMode) return;
    sendDrive();
    driveTimer = setInterval(sendDrive, 120);
}
function stopDriveLoop() {
    if (driveTimer) { clearInterval(driveTimer); driveTimer = null; }
}

function addDir(dir) {
    if (!manualMode) return;
    if (dir === 'stop') { active.clear(); stopDriveLoop(); sendDrive(); return; }
    active.add(dir);
    startDriveLoop();
}
function removeDir(dir) {
    active.delete(dir);
    if (active.size === 0) { stopDriveLoop(); sendDrive(); }   // final zero
}

const KEYDIR = { w: 'fwd', s: 'back', a: 'left', d: 'right',
                 arrowup: 'fwd', arrowdown: 'back', arrowleft: 'left', arrowright: 'right' };

if (TASK === 'lead') {
    const typing = (e) => e.target && e.target.tagName === 'INPUT';  // don't hijack slider arrows
    window.addEventListener('keydown', (e) => {
        if (!manualMode || typing(e)) return;
        const dir = KEYDIR[e.key.toLowerCase()];
        if (!dir) return;
        e.preventDefault();
        if (!e.repeat) addDir(dir);
    });
    window.addEventListener('keyup', (e) => {
        if (typing(e)) return;
        const dir = KEYDIR[e.key.toLowerCase()];
        if (dir) { e.preventDefault(); removeDir(dir); }
    });
    // Safety: drop control if the page loses focus or is hidden.
    window.addEventListener('blur', () => { if (active.size) { active.clear(); stopDriveLoop(); sendDrive(); } });

    document.querySelectorAll('.padbtn').forEach((btn) => {
        const dir = btn.dataset.dir;
        const press   = (e) => { e.preventDefault(); btn.classList.add('active');    addDir(dir); };
        const release = (e) => { e.preventDefault(); btn.classList.remove('active'); if (dir !== 'stop') removeDir(dir); };
        btn.addEventListener('pointerdown', press);
        btn.addEventListener('pointerup', release);
        btn.addEventListener('pointerleave', release);
        btn.addEventListener('pointercancel', release);
    });
}
'''

_JS_MAP = '''
// ---------------------------------------------------------------------------
// Top-down track map. Static layer (tiles/markings/objects) is rendered once
// to an offscreen canvas; the live layer (trail + robot) on top each frame.
// World axes: x -> right (east), z -> down (south); heading is the angle of
// the forward vector, fwd = (sin h, cos h) in (x, z).
// ---------------------------------------------------------------------------
const C = document.getElementById('map-canvas');
const PAD = 10;
const DIRS = ['N', 'E', 'S', 'W'];
const BASE_CONNS = { straight: [0, 2], curve: [0, 3], cross3: [0, 1, 3], cross: [0, 1, 2, 3] };
const APPROACH_ANGLE = { N: 0, E: Math.PI / 2, S: Math.PI, W: -Math.PI / 2 };

let SCALE = 1, DPR = 1, bg = null;
let poseNow = null, poseTarget = null, poseFrom = null, poseT0 = 0;
let trail = [];

function wx(x) { return PAD + (x - MAP.bounds.min_x) * SCALE; }
function wz(z) { return PAD + (z - MAP.bounds.min_z) * SCALE; }
function clearTrail() { trail = []; poseNow = null; poseFrom = null; poseTarget = null; }

function conns(t) {
    const k = ((Math.round(t.rot / 90) % 4) + 4) % 4;
    return BASE_CONNS[t.kind].map(i => DIRS[(i - k + 8) % 4]);
}

function line(g, x1, y1, x2, y2) {
    g.beginPath(); g.moveTo(x1, y1); g.lineTo(x2, y2); g.stroke();
}

function drawStraight(g, t, vertical) {
    const cx = wx(t.x), cz = wz(t.z);
    const h = 0.3 * SCALE, e = 0.275 * SCALE;
    g.strokeStyle = '#e8e8e8'; g.lineWidth = Math.max(1, 0.024 * SCALE);
    if (vertical) { line(g, cx - e, cz - h, cx - e, cz + h); line(g, cx + e, cz - h, cx + e, cz + h); }
    else          { line(g, cx - h, cz - e, cx + h, cz - e); line(g, cx - h, cz + e, cx + h, cz + e); }
    g.strokeStyle = '#f7d038'; g.setLineDash([0.07 * SCALE, 0.07 * SCALE]);
    if (vertical) line(g, cx, cz - h, cx, cz + h); else line(g, cx - h, cz, cx + h, cz);
    g.setLineDash([]);
}

function drawCurve(g, t, cs) {
    const cornerX = wx(t.x + (cs.includes('W') ? -0.3 : 0.3));
    const cornerZ = wz(t.z + (cs.includes('N') ? -0.3 : 0.3));
    g.save();
    g.beginPath();
    g.rect(wx(t.x) - 0.3 * SCALE, wz(t.z) - 0.3 * SCALE, 0.6 * SCALE, 0.6 * SCALE);
    g.clip();
    g.strokeStyle = '#e8e8e8'; g.lineWidth = Math.max(1, 0.024 * SCALE);
    g.beginPath(); g.arc(cornerX, cornerZ, 0.575 * SCALE, 0, 2 * Math.PI); g.stroke();
    g.beginPath(); g.arc(cornerX, cornerZ, 0.06 * SCALE, 0, 2 * Math.PI); g.stroke();
    g.strokeStyle = '#f7d038'; g.setLineDash([0.07 * SCALE, 0.07 * SCALE]);
    g.beginPath(); g.arc(cornerX, cornerZ, 0.3 * SCALE, 0, 2 * Math.PI); g.stroke();
    g.setLineDash([]);
    g.restore();
}

function drawCrossing(g, t, cs) {
    for (const d of cs) {
        g.save();
        g.translate(wx(t.x), wz(t.z));
        g.rotate(APPROACH_ANGLE[d]);
        // local frame: this approach's edge is at the top; the incoming
        // (right-hand) lane is the left half.
        g.fillStyle = '#e23a3a';
        g.fillRect(-0.265 * SCALE, -0.245 * SCALE, 0.25 * SCALE, 0.06 * SCALE);
        g.fillStyle = '#f7d038';
        g.fillRect(-0.012 * SCALE, -0.245 * SCALE, 0.05 * SCALE, 0.06 * SCALE);
        g.strokeStyle = '#e8e8e8'; g.lineWidth = Math.max(1, 0.024 * SCALE);
        line(g, -0.275 * SCALE, -0.3 * SCALE, -0.275 * SCALE, -0.16 * SCALE);
        line(g,  0.275 * SCALE, -0.3 * SCALE,  0.275 * SCALE, -0.16 * SCALE);
        g.restore();
    }
    if (t.kind === 'cross3') {
        const closed = DIRS.find(d => !cs.includes(d));
        g.save();
        g.translate(wx(t.x), wz(t.z));
        g.rotate(APPROACH_ANGLE[closed]);
        g.strokeStyle = '#e8e8e8'; g.lineWidth = Math.max(1, 0.024 * SCALE);
        line(g, -0.3 * SCALE, -0.275 * SCALE, 0.3 * SCALE, -0.275 * SCALE);
        g.restore();
    }
}

function drawStatic(g) {
    g.setTransform(DPR, 0, 0, DPR, 0, 0);
    g.fillStyle = '#243a1c';
    g.fillRect(0, 0, C.width, C.height);
    g.fillStyle = '#26292e';
    for (const t of MAP.tiles)
        g.fillRect(wx(t.x) - 0.3 * SCALE, wz(t.z) - 0.3 * SCALE, 0.6 * SCALE + 0.75, 0.6 * SCALE + 0.75);
    for (const t of MAP.tiles) {
        const cs = conns(t);
        if (t.kind === 'straight') drawStraight(g, t, cs.includes('N'));
        else if (t.kind === 'curve') drawCurve(g, t, cs);
        else drawCrossing(g, t, cs);
    }
    if (MAP.npc_path) {
        g.strokeStyle = 'rgba(34, 211, 238, 0.45)'; g.lineWidth = 1.5;
        g.setLineDash([5, 4]);
        g.beginPath();
        MAP.npc_path.forEach((p, i) => i ? g.lineTo(wx(p[0]), wz(p[1])) : g.moveTo(wx(p[0]), wz(p[1])));
        g.stroke(); g.setLineDash([]);
        const s = MAP.npc_path[0];
        g.strokeStyle = '#22d3ee'; g.lineWidth = 2;
        g.beginPath(); g.arc(wx(s[0]), wz(s[1]), 0.06 * SCALE, 0, 2 * Math.PI); g.stroke();
    }
    for (const duck of MAP.ducks) {
        g.fillStyle = '#f7d038';
        g.beginPath(); g.arc(wx(duck.x), wz(duck.z), 0.05 * SCALE, 0, 2 * Math.PI); g.fill();
        g.fillStyle = '#e07f24';
        g.beginPath(); g.arc(wx(duck.x) + 0.025 * SCALE, wz(duck.z), 0.018 * SCALE, 0, 2 * Math.PI); g.fill();
    }
    for (const sign of MAP.signs) {
        g.fillStyle = sign.kind === 'sign_stop' ? '#e23a3a' : '#1f6feb';
        g.beginPath(); g.arc(wx(sign.x), wz(sign.z), 0.04 * SCALE, 0, 2 * Math.PI); g.fill();
        g.strokeStyle = '#fff'; g.lineWidth = 1; g.stroke();
    }
    if (MAP.bot) {  // spawn marker
        g.strokeStyle = 'rgba(230,237,243,0.6)'; g.lineWidth = 1.5;
        g.beginPath(); g.arc(wx(MAP.bot.x), wz(MAP.bot.z), 0.05 * SCALE, 0, 2 * Math.PI); g.stroke();
    }
}

function setupMap() {
    if (!MAP || !MAP.tiles || !MAP.tiles.length) {
        C.style.display = 'none';
        setText('map-note', 'map scene not found');
        return false;
    }
    const holder = C.parentElement;
    const cssW = Math.max(200, holder.clientWidth - 24);
    const bw = MAP.bounds.max_x - MAP.bounds.min_x;
    const bh = MAP.bounds.max_z - MAP.bounds.min_z;
    SCALE = (cssW - 2 * PAD) / bw;
    const cssH = bh * SCALE + 2 * PAD;
    DPR = window.devicePixelRatio || 1;
    C.style.width = cssW + 'px'; C.style.height = cssH + 'px';
    C.width = Math.round(cssW * DPR); C.height = Math.round(cssH * DPR);
    bg = document.createElement('canvas');
    bg.width = C.width; bg.height = C.height;
    drawStatic(bg.getContext('2d'));
    return true;
}

let leaderPose = null;

function onPose(p, lp) {
    if (!MAP_OK) return;  // no map card; keep the 'map scene not found' note
    leaderPose = lp || null;
    const srcEl = document.getElementById('pose-source');
    if (!p) {
        srcEl.textContent = 'no pose';
        srcEl.style.color = 'var(--text-muted)';
        setText('map-note', SIM ? 'live pose unavailable \\u2014 waiting for Godot (circle = spawn)'
                                : 'no localization on the real robot \\u2014 map for reference (circle = sim spawn)');
        return;
    }
    srcEl.textContent = 'live';
    srcEl.style.color = 'var(--accent-green)';
    setText('map-note', 'x ' + p.x.toFixed(2) + '  z ' + p.z.toFixed(2) + ' m');
    poseFrom = poseNow || p;
    poseTarget = p;
    poseT0 = performance.now();
    const last = trail[trail.length - 1];
    if (!last || Math.hypot(p.x - last[0], p.z - last[1]) > 0.015) {
        trail.push([p.x, p.z]);
        if (trail.length > 900) trail.splice(0, 100);
    }
}

function lerpAngle(a, b, t) {
    let d = b - a;
    while (d > Math.PI) d -= 2 * Math.PI;
    while (d < -Math.PI) d += 2 * Math.PI;
    return a + d * t;
}

function drawLive() {
    requestAnimationFrame(drawLive);
    if (!bg) return;
    const g = C.getContext('2d');
    g.setTransform(1, 0, 0, 1, 0, 0);
    g.drawImage(bg, 0, 0);
    g.setTransform(DPR, 0, 0, DPR, 0, 0);

    if (trail.length > 1) {
        g.strokeStyle = TASK === 'lead' ? 'rgba(31,111,235,0.55)' : 'rgba(163,113,247,0.55)';
        g.lineWidth = 2;
        g.beginPath();
        trail.forEach((p, i) => i ? g.lineTo(wx(p[0]), wz(p[1])) : g.moveTo(wx(p[0]), wz(p[1])));
        g.stroke();
    }
    if (leaderPose) {  // live NPC leader (follow scene)
        g.fillStyle = '#22d3ee';
        g.beginPath(); g.arc(wx(leaderPose.x), wz(leaderPose.z), 0.05 * SCALE, 0, 2 * Math.PI); g.fill();
        g.strokeStyle = '#fff'; g.lineWidth = 1; g.stroke();
    }
    if (poseTarget) {
        const t = Math.min(1, (performance.now() - poseT0) / 260);
        poseNow = {
            x: poseFrom.x + (poseTarget.x - poseFrom.x) * t,
            z: poseFrom.z + (poseTarget.z - poseFrom.z) * t,
            theta: lerpAngle(poseFrom.theta, poseTarget.theta, t),
        };
        const px = wx(poseNow.x), pz = wz(poseNow.z);
        const fx = Math.sin(poseNow.theta), fz = Math.cos(poseNow.theta);
        const rx = fz, rz = -fx;
        const len = 0.085 * SCALE, wid = 0.055 * SCALE;
        g.fillStyle = TASK === 'lead' ? '#1f6feb' : '#a371f7';
        g.strokeStyle = '#fff'; g.lineWidth = 1;
        g.beginPath();
        g.moveTo(px + fx * len, pz + fz * len);
        g.lineTo(px - fx * len + rx * wid, pz - fz * len + rz * wid);
        g.lineTo(px - fx * len - rx * wid, pz - fz * len - rz * wid);
        g.closePath(); g.fill(); g.stroke();
    }
}

let MAP_OK = setupMap();
if (MAP_OK) requestAnimationFrame(drawLive);
window.addEventListener('resize', () => { if (MAP_OK) setupMap(); });
'''


def get_template(task, title, subtitle, sim=True):
    """task: 'lead' or 'follow'. sim=False relabels the run controls for the
    real robot (no Godot: /reset restarts the agent, pose stays null). Returns
    a Jinja template string expecting one variable: map_json (JSON or 'null')."""
    assert task in ('lead', 'follow')
    js = (_JS_COMMON + _JS_MAP).replace('__TASK__', task).replace('__SIM__', 'true' if sim else 'false')
    return render_template(title, subtitle, _content(task, sim=sim), extra_css=_EXTRA_CSS, extra_js=js)
