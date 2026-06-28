from .base import render_template

_EXTRA_CSS = '''
.key-display {
    display: grid;
    grid-template-areas: ".    up   ." "left down right";
    grid-template-columns: repeat(3, 48px);
    grid-template-rows: repeat(2, 48px);
    gap: 4px;
    justify-content: center;
    margin: 8px 0;
}
.key-box {
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--bg-sidebar);
    border: 2px solid var(--border-color);
    border-radius: 8px;
    font-size: 20px;
    font-weight: 600;
    color: var(--text-muted);
    transition: all 0.1s;
    user-select: none;
}
.key-box.active { background: rgba(63,185,80,0.2); border-color: var(--accent-green); color: var(--accent-green); }
.key-up    { grid-area: up; }
.key-down  { grid-area: down; }
.key-left  { grid-area: left; }
.key-right { grid-area: right; }

.speed-display { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 8px; }
.speed-box { text-align: center; padding: 8px; background: var(--bg-sidebar); border: 1px solid var(--border-color); border-radius: 6px; }
.speed-value { font-size: 22px; font-weight: 700; font-family: monospace; color: var(--accent-blue); }
.speed-label { font-size: 11px; color: var(--text-muted); text-transform: uppercase; margin-top: 3px; }

.instructions { font-size: 12px; color: var(--text-secondary); line-height: 1.6; }
.instructions code { background: var(--bg-sidebar); padding: 2px 6px; border-radius: 3px; font-size: 11px; color: var(--accent-orange); }
.file-path { background: var(--bg-sidebar); border: 1px solid var(--border-color); border-radius: 4px; padding: 8px 12px; font-family: monospace; font-size: 12px; color: var(--accent-green); margin: 8px 0; word-break: break-all; }

.led-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 12px; }
.led-box { background: var(--bg-sidebar); border: 1px solid var(--border-color); border-radius: 6px; padding: 10px; text-align: center; }
.led-label { font-size: 11px; color: var(--text-muted); margin-bottom: 6px; }
.led-preview { width: 32px; height: 32px; border-radius: 50%; margin: 0 auto 8px; border: 2px solid var(--border-color); background: #000; }
.led-color-input { width: 100%; height: 28px; border: none; border-radius: 4px; cursor: pointer; background: transparent; }
.led-buttons { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 6px; margin-top: 8px; }
.led-btn { padding: 6px; font-size: 11px; border: 1px solid var(--border-color); border-radius: 4px; cursor: pointer; background: var(--bg-sidebar); color: var(--text-secondary); font-family: 'Inter', sans-serif; }
.led-btn:hover { border-color: var(--accent-blue); color: var(--text-primary); }
'''

_CONTENT = '''
    <div class="container">
        <div class="video-section">
            <img src="{{ url_for('video') }}" class="stream">
        </div>

        <div class="controls-section">
            <div class="card">
                <div class="card-header">Controls</div>
                <div class="key-display">
                    <div class="key-box key-up"    id="key-up">&#9650;</div>
                    <div class="key-box key-left"  id="key-left">&#9664;</div>
                    <div class="key-box key-down"  id="key-down">&#9660;</div>
                    <div class="key-box key-right" id="key-right">&#9654;</div>
                </div>
                <p style="text-align:center;font-size:11px;color:var(--text-muted)">Arrow keys or WASD</p>
                <div class="speed-display">
                    <div class="speed-box"><div class="speed-value" id="speed-left">0.00</div><div class="speed-label">Left wheel</div></div>
                    <div class="speed-box"><div class="speed-value" id="speed-right">0.00</div><div class="speed-label">Right wheel</div></div>
                </div>
            </div>

            <div class="card">
                <div class="card-header">Assignment</div>
                <div class="instructions">
                    <p>Edit the student file and implement <code>get_motor_speeds()</code>:</p>
                    <div class="file-path">tasks/introduction/packages/manual_drive.py</div>
                    <p style="margin-top:8px">
                        Forward = both wheels positive.<br>
                        Turn = one wheel faster than the other.<br>
                        Restart the server after editing.
                    </p>
                </div>
            </div>
        </div>
    </div>
'''

_JS = '''
    const keyState = {up: false, down: false, left: false, right: false};
    const keyMap = {
        'ArrowUp': 'up', 'ArrowDown': 'down', 'ArrowLeft': 'left', 'ArrowRight': 'right',
        'w': 'up', 's': 'down', 'a': 'left', 'd': 'right',
        'W': 'up', 'S': 'down', 'A': 'left', 'D': 'right',
    };

    function updateKeyDisplay() {
        for (const [key, active] of Object.entries(keyState)) {
            const el = document.getElementById('key-' + key);
            if (el) el.classList.toggle('active', active);
        }
    }

    function sendKeys() {
        fetch('/keys', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(keyState)})
            .then(r => r.json())
            .then(data => {
                document.getElementById('speed-left').textContent  = data.left.toFixed(2);
                document.getElementById('speed-right').textContent = data.right.toFixed(2);
            }).catch(() => {});
    }

    function releaseAll() {
        let changed = Object.values(keyState).some(Boolean);
        Object.keys(keyState).forEach(k => keyState[k] = false);
        if (changed) { updateKeyDisplay(); sendKeys(); }
    }

    document.addEventListener('keydown', e => {
        const dir = keyMap[e.key];
        if (dir && !keyState[dir]) { e.preventDefault(); keyState[dir] = true; updateKeyDisplay(); sendKeys(); }
    });
    document.addEventListener('keyup', e => {
        const dir = keyMap[e.key];
        if (dir && keyState[dir]) { e.preventDefault(); keyState[dir] = false; updateKeyDisplay(); sendKeys(); }
    });

    // Release keys when page loses focus (tab switch, alt-tab, click away)
    window.addEventListener('blur', releaseAll);
    document.addEventListener('visibilitychange', () => { if (document.hidden) releaseAll(); });

    // Heartbeat: while any key is held, re-send state every 150ms so a missed
    // keyup can't leave the server stuck for more than one heartbeat interval
    setInterval(() => {
        if (Object.values(keyState).some(Boolean)) sendKeys();
    }, 150);

    setInterval(() => {
        fetch('/speeds').then(r => r.json()).then(data => {
            document.getElementById('speed-left').textContent  = data.left.toFixed(2);
            document.getElementById('speed-right').textContent = data.right.toFixed(2);
        }).catch(() => {});
    }, 500);

    function hexToRgb(hex) {
        return [parseInt(hex.slice(1,3),16)/255, parseInt(hex.slice(3,5),16)/255, parseInt(hex.slice(5,7),16)/255];
    }
    function rgbToHex(c) {
        return '#' + [c[0],c[1],c[2]].map(v => Math.round(v*255).toString(16).padStart(2,'0')).join('');
    }
    function updateLedPreview(led, color) {
        const preview = document.getElementById('led-preview-' + led);
        const picker  = document.getElementById('led-color-'   + led);
        if (preview) preview.style.background = rgbToHex(color);
        if (picker)  picker.value = rgbToHex(color);
    }
    function setLedFromPicker(led, hex) {
        const color = hexToRgb(hex);
        updateLedPreview(led, color);
        postJSON('/leds', {led, color});
    }
    function setAllLeds(color) {
        [0,2,3,4].forEach(led => updateLedPreview(led, color));
        postJSON('/leds/all', {color});
    }
    function ledsOff() {
        [0,2,3,4].forEach(led => updateLedPreview(led, [0,0,0]));
        postJSON('/leds/off', {});
    }
'''

INTRODUCTION_TEMPLATE = render_template(
    '{{ title }}',
    '{{ subtitle }}',
    _CONTENT,
    extra_css=_EXTRA_CSS,
    extra_js=_JS,
)
