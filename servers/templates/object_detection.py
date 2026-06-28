from .base import render_template

_EXTRA_CSS = '''
.key-display {
    display: grid;
    grid-template-areas: ". up ." "left down right";
    gap: 4px;
    justify-content: center;
    margin: 8px 0 4px;
}
.key-box {
    width: 32px; height: 32px;
    display: flex; align-items: center; justify-content: center;
    border: 1px solid var(--border-color);
    border-radius: 4px;
    font-size: 13px; font-weight: 700;
    color: var(--text-muted);
    background: var(--bg-sidebar);
    transition: background 0.1s, border-color 0.1s, color 0.1s;
}
.key-box.active { background: rgba(63,185,80,0.2); border-color: var(--accent-green); color: var(--accent-green); }
.key-up    { grid-area: up; }
.key-down  { grid-area: down; }
.key-left  { grid-area: left; }
.key-right { grid-area: right; }

.video-wrapper {
    position: relative;
    display: inline-block;
    line-height: 0;
}
.stop-banner {
    display: none;
    position: absolute;
    top: 10px;
    left: 50%;
    transform: translateX(-50%);
    background: rgba(192,57,43,0.88);
    color: #fff;
    text-align: center;
    font-size: 13px;
    font-weight: 700;
    padding: 6px 16px;
    border-radius: 4px;
    letter-spacing: 0.5px;
    white-space: nowrap;
    z-index: 10;
    pointer-events: none;
}
.stop-banner.active { display: block; }
.stream.stopped { outline: 3px solid #c0392b; }

.detections-list {
    display: flex;
    flex-direction: column;
    gap: 6px;
    max-height: 200px;
    overflow-y: auto;
}
.det-row {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 5px 8px;
    background: var(--bg-sidebar);
    border: 1px solid var(--border-color);
    border-radius: 4px;
    font-size: 12px;
}
.det-class { font-weight: 600; color: var(--text-primary); }
.det-score { color: var(--text-secondary); font-variant-numeric: tabular-nums; }
.det-bbox  { color: var(--text-muted); font-size: 11px; font-variant-numeric: tabular-nums; }

.empty-state { color: var(--text-muted); font-size: 12px; text-align: center; padding: 12px; }

.model-status { padding: 6px 10px; border-radius: 4px; font-size: 12px; margin-bottom: 10px; }
.model-status.ok      { background: rgba(63,185,80,0.1);  border: 1px solid rgba(63,185,80,0.3);  color: var(--accent-green); }
.model-status.err     { background: rgba(248,81,73,0.1);  border: 1px solid rgba(248,81,73,0.3);  color: var(--accent-red); }
.model-status.building{ background: rgba(210,153,34,0.1); border: 1px solid rgba(210,153,34,0.3); color: #d6a63a; }

.trt-build-card { display: none; }
.trt-build-hint { font-size: 11px; color: var(--text-muted); margin-bottom: 8px; }
.trt-ready      { font-size: 15px; font-weight: 700; color: var(--accent-green); text-align: center; padding: 6px 0; }
'''

_CONTENT = '''
    <div class="container">
        <div class="video-section">
            <div class="video-wrapper">
                <div id="stop-banner" class="stop-banner"></div>
                <img src="{{ url_for('video') }}" id="stream-img" class="stream">
            </div>
        </div>

        <div class="controls-section">
            <div class="card">
                <div class="card-header">Drive Control</div>
                <div style="display:flex;align-items:center;gap:16px;margin-bottom:12px">
                    <span id="run-indicator" style="display:inline-block;width:14px;height:14px;border-radius:50%;background:#e74c3c;flex-shrink:0"></span>
                    <span id="run-label" style="font-size:14px;font-weight:600;color:var(--text-secondary)">STOPPED</span>
                </div>
                <div style="display:flex;gap:10px;margin-bottom:8px">
                    <button onclick="driveStart()" class="button success" style="flex:1">Start</button>
                    <button onclick="driveStop()"  class="button" style="flex:1;background:var(--accent-orange,#e67e22)">Stop</button>
                </div>
                {% if virtual %}
                <div style="display:flex;gap:10px;margin-bottom:8px">
                    <button id="mode-btn" onclick="toggleMode()" class="button" style="flex:1;background:#555">Manual</button>
                    <button onclick="resetPosition()" class="button" style="flex:1;background:#444">Reset</button>
                </div>
                {% else %}
                <div style="margin-bottom:8px">
                    <button id="mode-btn" onclick="toggleMode()" class="button" style="width:100%;background:#555">Manual</button>
                </div>
                {% endif %}
                <div id="key-panel" style="display:none">
                    <div class="key-display">
                        <div class="key-box key-up"    id="key-up">&#9650;</div>
                        <div class="key-box key-left"  id="key-left">&#9664;</div>
                        <div class="key-box key-down"  id="key-down">&#9660;</div>
                        <div class="key-box key-right" id="key-right">&#9654;</div>
                    </div>
                    <p style="text-align:center;font-size:11px;color:var(--text-muted);margin:4px 0 0">Arrow keys or WASD</p>
                </div>
            </div>

            {% if virtual %}
            <div class="card">
                <div class="card-header">Scene Objects</div>
                <button onclick="removeObjects('duckie')" class="button" style="width:100%;background:#555">Remove Duck &amp; Continue</button>
            </div>
            <div class="card">
                <div class="card-header">Map</div>
                <button id="scene-btn" onclick="switchScene()" class="button" style="width:100%;background:#446">Switch to Free Drive</button>
            </div>
            {% endif %}

            <div class="card trt-build-card" id="trt-build-card">
                <div class="card-header" id="trt-header">Building TensorRT Engine</div>
                <p class="trt-build-hint">Camera and driving work now — detection starts when done.</p>
                <div class="trt-ready" id="trt-ready" style="display:none">Detection started!</div>
            </div>

            <div class="card">
                <div class="card-header">Confidence Threshold</div>
                <div style="display:flex;align-items:center;gap:10px">
                    <input id="conf-slider" type="range" min="0" max="1" step="0.01" value="0.5"
                        style="flex:1" oninput="onThresholdChange(this.value)">
                    <span id="conf-value" style="font-size:13px;font-variant-numeric:tabular-nums;min-width:32px">0.50</span>
                </div>
            </div>

            <div class="card">
                <div class="card-header">
                    Detection
                    <span style="font-size:11px;font-weight:400;color:var(--text-muted)" id="det-count"></span>
                </div>
                <div id="model-status" class="model-status ok">Loading…</div>
                <div id="detections" class="detections-list">
                    <div class="empty-state">Waiting for frames…</div>
                </div>
            </div>
        </div>
    </div>
'''

_EXTRA_JS = '''
    function setRunningUI(isRunning) {
        const indicator = document.getElementById('run-indicator');
        const label     = document.getElementById('run-label');
        indicator.style.background = isRunning ? '#2ecc71' : '#e74c3c';
        label.textContent = isRunning ? 'RUNNING' : 'STOPPED';
        label.style.color = isRunning ? '#2ecc71' : 'var(--text-secondary)';
    }

    function driveStart() {
        postJSON('/start', {}).then(() => setRunningUI(true));
    }

    function driveStop() {
        postJSON('/stop', {}).then(() => setRunningUI(false));
    }

    function removeObjects(filter) {
        postJSON('/remove_objects', {filter: filter});
    }

    function resetPosition() {
        postJSON('/reset', {}).then(data => {
            if (data && data.running !== undefined) setRunningUI(data.running);
        });
    }

    let _currentScene = 'object_detection';
    function switchScene() {
        const target = _currentScene === 'object_detection' ? 'introduction' : 'object_detection';
        postJSON('/switch_scene', {scene: target}).then(data => {
            if (data && data.scene) {
                _currentScene = data.scene;
                _updateSceneBtn();
                _manualMode = !!data.manual_mode;
                const btn = document.getElementById('mode-btn');
                const panel = document.getElementById('key-panel');
                if (btn) btn.textContent = _manualMode ? 'Auto' : 'Manual';
                if (panel) panel.style.display = _manualMode ? 'block' : 'none';
            }
        });
    }
    function _updateSceneBtn() {
        const btn = document.getElementById('scene-btn');
        if (!btn) return;
        if (_currentScene === 'introduction') {
            btn.textContent = 'Switch to Lane Follow';
        } else {
            btn.textContent = 'Switch to Free Drive';
        }
    }

    let _manualMode = false;
    const keyState = {up: false, down: false, left: false, right: false};
    const keyMap = {
        'ArrowUp': 'up', 'w': 'up', 'W': 'up',
        'ArrowDown': 'down', 's': 'down', 'S': 'down',
        'ArrowLeft': 'left', 'a': 'left', 'A': 'left',
        'ArrowRight': 'right', 'd': 'right', 'D': 'right',
    };

    function updateKeyDisplay() {
        for (const [key, active] of Object.entries(keyState)) {
            const el = document.getElementById('key-' + key);
            if (el) el.classList.toggle('active', active);
        }
    }

    function sendKeys() {
        fetch('/keys', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(keyState)}).catch(() => {});
    }

    function toggleMode() {
        _manualMode = !_manualMode;
        postJSON('/set_mode', {mode: _manualMode ? 'manual' : 'auto'});
        const btn = document.getElementById('mode-btn');
        const panel = document.getElementById('key-panel');
        if (btn)   btn.textContent = _manualMode ? 'Auto' : 'Manual';
        if (panel) panel.style.display = _manualMode ? 'block' : 'none';
    }

    document.addEventListener('keydown', e => {
        const dir = keyMap[e.key];
        if (dir && !keyState[dir]) { e.preventDefault(); keyState[dir] = true; updateKeyDisplay(); if (_manualMode) sendKeys(); }
    });
    document.addEventListener('keyup', e => {
        const dir = keyMap[e.key];
        if (dir && keyState[dir]) { e.preventDefault(); keyState[dir] = false; updateKeyDisplay(); if (_manualMode) sendKeys(); }
    });
    window.addEventListener('blur', () => {
        Object.keys(keyState).forEach(k => keyState[k] = false);
        updateKeyDisplay(); if (_manualMode) sendKeys();
    });
    setInterval(() => { if (_manualMode && Object.values(keyState).some(Boolean)) sendKeys(); }, 150);

    let _sliderDirty = false;
    function onThresholdChange(value) {
        document.getElementById('conf-value').textContent = parseFloat(value).toFixed(2);
        _sliderDirty = true;
        postJSON('/set_threshold', {value: parseFloat(value)}).then(() => { _sliderDirty = false; });
    }

    async function pollStatus() {
        try {
            const data = await fetch('/status').then(r => r.json());

            // Running state
            setRunningUI(data.running);

            // Scene sync
            if (data.current_scene && data.current_scene !== _currentScene) {
                _currentScene = data.current_scene;
                _updateSceneBtn();
            }

            // Stop banner + red outline on video
            const banner = document.getElementById('stop-banner');
            const streamImg = document.getElementById('stream-img');
            if (data.stopped_by_detection) {
                banner.textContent = 'STOPPED — ' + data.stop_reason;
                banner.classList.add('active');
                streamImg.classList.add('stopped');
            } else {
                banner.classList.remove('active');
                streamImg.classList.remove('stopped');
            }

            // TRT build card
            const trtCard = document.getElementById('trt-build-card');
            if (data.trt_building) {
                trtCard.style.display = 'block';
            } else if (trtCard._wasBuilding) {
                document.getElementById('trt-header').textContent = 'TensorRT Engine Ready';
                document.getElementById('trt-ready').style.display = 'block';
                setTimeout(() => { trtCard.style.display = 'none'; trtCard._wasBuilding = false; }, 4000);
            } else {
                trtCard.style.display = 'none';
            }
            trtCard._wasBuilding = data.trt_building;

            // Model status chip
            const status = document.getElementById('model-status');
            if (data.status === 'not_initialized') {
                status.className = 'model-status building';
                status.textContent = 'Initializing…';
            } else if (data.trt_building) {
                status.className = 'model-status building';
                status.textContent = 'Building TensorRT engine…';
            } else if (data.model_loaded) {
                status.className = 'model-status ok';
                status.textContent = 'Model loaded';
            } else {
                status.className = 'model-status err';
                status.textContent = data.load_error || 'Model not loaded';
            }

            // Sync slider only when user isn't actively dragging
            if (!_sliderDirty && data.conf_threshold != null) {
                const slider = document.getElementById('conf-slider');
                slider.value = data.conf_threshold;
                document.getElementById('conf-value').textContent = data.conf_threshold.toFixed(2);
            }

            const dets = data.detections || [];
            document.getElementById('det-count').textContent = dets.length ? dets.length + ' found' : '';

            const list = document.getElementById('detections');
            list.innerHTML = dets.length === 0
                ? '<div class="empty-state">No detections</div>'
                : dets.map(d => `
                    <div class="det-row">
                        <span class="det-class">${d.class}</span>
                        <span class="det-score">${d.score.toFixed(2)}</span>
                        <span class="det-bbox">[${d.bbox.join(', ')}]</span>
                    </div>`).join('');
        } catch (e) {}
    }

    setInterval(pollStatus, 300);
    pollStatus();
'''

OBJECT_DETECTION_TEMPLATE = render_template(
    'Object Detection',
    '{{ hostname }} — Drive & Detect',
    _CONTENT,
    extra_css=_EXTRA_CSS,
    extra_js=_EXTRA_JS,
)
