from .base import render_template

_CONTENT = '''
    <div class="container">
        <div class="video-section">
            <img src="{{ url_for('video') }}" class="stream">
        </div>

        <div class="controls-section">
            <div class="card">
                <div class="card-header">Motor Output</div>
                <div class="motor-viz">
                    <div class="motor-col">
                        <div class="motor-bar-wrap">
                            <div class="motor-bar-fill motor-left" id="motorLeftFill"></div>
                        </div>
                        <div class="motor-num" id="motorLeftVal">--</div>
                        <div class="motor-lbl">LEFT</div>
                    </div>
                    <div class="motor-col">
                        <div class="motor-bar-wrap">
                            <div class="motor-bar-fill motor-right" id="motorRightFill"></div>
                        </div>
                        <div class="motor-num" id="motorRightVal">--</div>
                        <div class="motor-lbl">RIGHT</div>
                    </div>
                </div>
            </div>

            <div class="card">
                <div class="card-header">HSV Color Calibration</div>

                <div class="slider-group">
                    <div class="slider-label"><span>Hue Low</span><span style="color:var(--text-muted)">0-179</span></div>
                    <div class="slider-controls">
                        <input type="range" id="lowH" min="0" max="179" value="30" class="slider">
                        <input type="number" id="lowH-input" min="0" max="179" value="30" class="input-box">
                    </div>
                </div>
                <div class="slider-group">
                    <div class="slider-label"><span>Hue High</span><span style="color:var(--text-muted)">0-179</span></div>
                    <div class="slider-controls">
                        <input type="range" id="highH" min="0" max="179" value="120" class="slider">
                        <input type="number" id="highH-input" min="0" max="179" value="120" class="input-box">
                    </div>
                </div>
                <div class="slider-group">
                    <div class="slider-label"><span>Saturation Low</span><span style="color:var(--text-muted)">0-255</span></div>
                    <div class="slider-controls">
                        <input type="range" id="lowS" min="0" max="255" value="90" class="slider">
                        <input type="number" id="lowS-input" min="0" max="255" value="90" class="input-box">
                    </div>
                </div>
                <div class="slider-group">
                    <div class="slider-label"><span>Saturation High</span><span style="color:var(--text-muted)">0-255</span></div>
                    <div class="slider-controls">
                        <input type="range" id="highS" min="0" max="255" value="255" class="slider">
                        <input type="number" id="highS-input" min="0" max="255" value="255" class="input-box">
                    </div>
                </div>
                <div class="slider-group">
                    <div class="slider-label"><span>Value Low</span><span style="color:var(--text-muted)">0-255</span></div>
                    <div class="slider-controls">
                        <input type="range" id="lowV" min="0" max="255" value="100" class="slider">
                        <input type="number" id="lowV-input" min="0" max="255" value="100" class="input-box">
                    </div>
                </div>
                <div class="slider-group">
                    <div class="slider-label"><span>Value High</span><span style="color:var(--text-muted)">0-255</span></div>
                    <div class="slider-controls">
                        <input type="range" id="highV" min="0" max="255" value="255" class="slider">
                        <input type="number" id="highV-input" min="0" max="255" value="255" class="input-box">
                    </div>
                </div>
            </div>

            <div class="card">
                <div class="card-header">
                    Agent Configuration
                    {% if show_game_stats %}
                    <button onclick="resetGame()" class="button danger" style="width:auto;padding:4px 12px;font-size:12px;margin:0">&#8635; Reset Sim</button>
                    {% endif %}
                </div>
                <div class="slider-group">
                    <div class="slider-label"><span>Base Speed</span><span style="color:var(--text-muted)">PWM [0–1]</span></div>
                    <input type="number" id="const-input" step="0.05" min="0" max="1" value="{{ config.const }}" class="input-box" style="width:100%">
                </div>
                <div class="slider-group">
                    <div class="slider-label"><span>Turn Gain</span><span style="color:var(--text-muted)">strength [0–3]</span></div>
                    <input type="number" id="gain-input" step="0.1" min="0" max="3" value="{{ config.gain }}" class="input-box" style="width:100%">
                </div>
                <div class="slider-group">
                    <div class="slider-label"><span>Detection Threshold</span></div>
                    <input type="number" id="threshold-input" step="100" min="0" max="10000" value="{{ config.detection_threshold }}" class="input-box" style="width:100%">
                </div>
                <button onclick="updateAgentConfig()" class="button">Apply Configuration</button>
                <div id="config-status" class="status"></div>
            </div>
        </div>
    </div>
'''

_JS = '''
    fetch('/get_hsv')
        .then(r => r.json())
        .then(data => {
            setSliderValue('lowH', data.lower_h);
            setSliderValue('highH', data.upper_h);
            setSliderValue('lowS', data.lower_s);
            setSliderValue('highS', data.upper_s);
            setSliderValue('lowV', data.lower_v);
            setSliderValue('highV', data.upper_v);
        });

    function sendHSVUpdate() {
        postJSON('/update_hsv', {
            lower_h: parseInt(document.getElementById('lowH').value),
            upper_h: parseInt(document.getElementById('highH').value),
            lower_s: parseInt(document.getElementById('lowS').value),
            upper_s: parseInt(document.getElementById('highS').value),
            lower_v: parseInt(document.getElementById('lowV').value),
            upper_v: parseInt(document.getElementById('highV').value)
        }).then(() => showStatus('config-status', 'HSV Updated!', 'success'));
    }

    ['lowH', 'highH', 'lowS', 'highS', 'lowV', 'highV'].forEach(id => syncSliderInput(id, sendHSVUpdate));

    function clamp(value, min, max) {
    return Math.max(min, Math.min(max, value));
}

function updateAgentConfig() {
    let gain = parseFloat(document.getElementById('gain-input').value);
    let speed = parseFloat(document.getElementById('const-input').value);

    gain = clamp(gain, 0, 3);
    speed = clamp(speed, 0, 1);

    document.getElementById('gain-input').value = gain;
    document.getElementById('const-input').value = speed;

    postJSON('/update_config', {
        gain: gain,
        const: speed,
        detection_threshold: parseFloat(document.getElementById('threshold-input').value)
    }).then(() => showStatus('config-status', 'Config Updated!', 'success'));
}

    function resetGame() {
        postJSON('/reset_game', {})
            .then(() => showStatus('config-status', 'Simulation reset!', 'success'))
            .catch(() => showStatus('config-status', 'Reset failed', 'error'));
    }
'''

_CSS = '''
.motor-viz {
    display: flex;
    gap: 20px;
    justify-content: center;
    padding: 4px 12px 0;
}
.motor-col {
    display: flex;
    flex-direction: column;
    align-items: center;
    flex: 1;
}
.motor-bar-wrap {
    width: 100%;
    height: 90px;
    background: var(--bg-sidebar);
    border: 1px solid var(--border-color);
    border-radius: 4px;
    position: relative;
    overflow: hidden;
}
.motor-bar-fill {
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    height: 0%;
    transition: height 0.12s ease;
}
.motor-left  { background: linear-gradient(0deg, #1a56b0, #388bfd); }
.motor-right { background: linear-gradient(0deg, #b03030, #f85149); }
.motor-num {
    font-size: 15px;
    font-weight: 600;
    margin-top: 5px;
    font-variant-numeric: tabular-nums;
    color: var(--text-primary);
}
.motor-lbl {
    font-size: 9px;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-top: 2px;
}
'''

_JS_MOTORS = '''
function pollMotors() {
    fetch('/get_motors')
        .then(r => r.json())
        .then(d => {
            const l = Math.max(0, Math.min(1, d.pwm_left  || 0));
            const r = Math.max(0, Math.min(1, d.pwm_right || 0));
            document.getElementById('motorLeftFill').style.height  = (l * 100) + '%';
            document.getElementById('motorRightFill').style.height = (r * 100) + '%';
            document.getElementById('motorLeftVal').textContent  = (d.pwm_left  != null ? d.pwm_left.toFixed(2)  : '--');
            document.getElementById('motorRightVal').textContent = (d.pwm_right != null ? d.pwm_right.toFixed(2) : '--');
        })
        .catch(() => {});
}
pollMotors();
setInterval(pollMotors, 500);
'''

BRAITENBERG_TEMPLATE = render_template('{{ title }}', '{{ subtitle }}', _CONTENT, extra_css=_CSS, extra_js=_JS + _JS_MOTORS)
