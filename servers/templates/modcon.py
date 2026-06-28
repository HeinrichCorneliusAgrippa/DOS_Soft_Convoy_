from .base import render_template

_CONTENT = '''
    <div class="container">
        <div class="video-section">
            <img src="{{ url_for('video') }}" class="stream">
        </div>

        <div class="controls-section">

            <!-- Live Status -->
            <div class="card">
                <div class="card-header">Live Status</div>
                <div class="stats-grid" style="grid-template-columns:1fr 1fr 1fr;margin-bottom:10px">
                    <div class="stat-box">
                        <div class="stat-value" id="pose-x">0.000</div>
                        <div class="stat-label">X (m)</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-value" id="pose-y">0.000</div>
                        <div class="stat-label">Y (m)</div>
                    </div>
                    <div class="stat-box">
                        <div class="stat-value" id="pose-theta">0.0</div>
                        <div class="stat-label">&#952; (&#176;)</div>
                    </div>
                </div>
                <div style="background:var(--bg-sidebar);border:1px solid var(--border-color);border-radius:4px;margin-bottom:8px">
                    <canvas id="path-canvas" width="320" height="180" style="width:100%;display:block;border-radius:4px"></canvas>
                </div>
                <div id="maneuver-status" style="font-size:11px;color:var(--text-secondary);text-align:center;min-height:16px;margin-bottom:6px"></div>
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px">
                    <button onclick="resetPose()" class="button">Reset Pose</button>
                    <button onclick="resetSim()" class="button danger">Reset Simulation</button>
                </div>
            </div>

            <!-- Maneuvers -->
            <div class="card">
                <div class="card-header">Maneuvers</div>

                <div class="slider-group">
                    <div class="slider-label"><span>Distance (m)</span></div>
                    <div class="slider-controls">
                        <input type="number" id="straight-dist" step="0.1" min="0.05" max="5" value="0.5" class="input-box" style="width:70px">
                        <button onclick="runManeuver('straight')" class="button" style="flex:1;margin:0">Drive Straight</button>
                    </div>
                </div>

                <div class="slider-group">
                    <div class="slider-label"><span>Rotation (&#176;)</span></div>
                    <div class="slider-controls">
                        <input type="number" id="turn-deg" step="5" min="-360" max="360" value="90" class="input-box" style="width:70px">
                        <button onclick="runManeuver('turn')" class="button" style="flex:1;margin:0">Turn by Degrees</button>
                    </div>
                </div>

                <div class="slider-group" style="margin-bottom:10px">
                    <div class="slider-label"><span>Side length (m)</span></div>
                    <div class="slider-controls">
                        <input type="number" id="square-side" step="0.1" min="0.1" max="2" value="0.5" class="input-box" style="width:70px">
                        <button onclick="runManeuver('square')" class="button" style="flex:1;margin:0">Run Square</button>
                    </div>
                </div>

                <button onclick="stopManeuver()" class="button danger">&#9632; Stop</button>
                <div id="maneuver-msg" class="status"></div>
            </div>

            <!-- PID Tuning -->
            <div class="card">
                <div class="card-header">PID Tuning</div>
                <div class="slider-group">
                    <div class="slider-label"><span>K_P</span><span style="color:var(--text-muted)">Proportional</span></div>
                    <input type="number" id="pid-kp" step="0.5" min="0" max="30" value="{{ pid_kp }}" class="input-box" style="width:100%">
                </div>
                <div class="slider-group">
                    <div class="slider-label"><span>K_I</span><span style="color:var(--text-muted)">Integral</span></div>
                    <input type="number" id="pid-ki" step="0.1" min="0" max="5" value="{{ pid_ki }}" class="input-box" style="width:100%">
                </div>
                <div class="slider-group">
                    <div class="slider-label"><span>K_D</span><span style="color:var(--text-muted)">Derivative</span></div>
                    <input type="number" id="pid-kd" step="0.1" min="0" max="5" value="{{ pid_kd }}" class="input-box" style="width:100%">
                </div>
                <div class="slider-group">
                    <div class="slider-label"><span>v&#8320; (m/s)</span><span style="color:var(--text-muted)">Maneuver speed</span></div>
                    <input type="number" id="robot-v0" step="0.05" min="0.1" max="1" value="{{ v_0 }}" class="input-box" style="width:100%">
                </div>
                <button onclick="applyPID()" class="button">Apply</button>
                <div id="pid-status" class="status"></div>
            </div>

            <!-- Wheel Calibration (real hardware only) -->
            {% if show_calibration %}
            <div class="card">
                <div class="card-header">Wheel Calibration</div>
                <p style="font-size:11px;color:var(--text-muted);margin-bottom:10px;line-height:1.6">
                    <b>Trim:</b> positive = right wheel gets more power (fixes left drift).<br>
                    Drive straight and adjust until the robot tracks a straight line.
                </p>
                <div class="slider-group">
                    <div class="slider-label">
                        <span>Trim</span>
                        <span style="color:var(--text-muted)">-0.5 to 0.5</span>
                    </div>
                    <div style="display:flex;gap:4px;align-items:center;margin-bottom:6px">
                        <input type="number" id="calib-trim" step="0.01" min="-0.5" max="0.5" value="{{ trim }}" class="input-box" style="width:72px">
                        <button onclick="adjustTrim(-0.05)" class="button" style="flex:1;margin:0;padding:5px 2px;font-size:11px">&#8722;0.05</button>
                        <button onclick="adjustTrim(-0.01)" class="button" style="flex:1;margin:0;padding:5px 2px;font-size:11px">&#8722;0.01</button>
                        <button onclick="adjustTrim(0.01)"  class="button" style="flex:1;margin:0;padding:5px 2px;font-size:11px">+0.01</button>
                        <button onclick="adjustTrim(0.05)"  class="button" style="flex:1;margin:0;padding:5px 2px;font-size:11px">+0.05</button>
                    </div>
                </div>
                <div class="slider-group">
                    <div class="slider-label"><span>Gain</span><span style="color:var(--text-muted)">0.1 to 1.0</span></div>
                    <input type="number" id="calib-gain" step="0.05" min="0.1" max="1.0" value="{{ gain }}" class="input-box" style="width:100%">
                </div>
                <button onclick="saveCalibration()" class="button">Apply</button>
                <div id="calib-status" class="status"></div>
            </div>
            {% endif %}


        </div>
    </div>
'''

_JS = '''
    // ── Path Canvas ───────────────────────────────────────────────────────
    const canvas = document.getElementById('path-canvas');
    const ctx = canvas.getContext('2d');

    function drawPath(path) {
        const W = canvas.width, H = canvas.height;
        ctx.clearRect(0, 0, W, H);
        ctx.fillStyle = '#0d1117';
        ctx.fillRect(0, 0, W, H);

        // Always include origin in bounds
        let minX = 0, maxX = 0, minY = 0, maxY = 0;
        for (const [x, y] of path) {
            minX = Math.min(minX, x); maxX = Math.max(maxX, x);
            minY = Math.min(minY, y); maxY = Math.max(maxY, y);
        }

        const pad = 22;
        const rangeX = Math.max(maxX - minX, 0.05);
        const rangeY = Math.max(maxY - minY, 0.05);
        const scale = Math.min((W - pad * 2) / rangeX, (H - pad * 2) / rangeY);

        // Centre the path
        const drawW = rangeX * scale;
        const drawH = rangeY * scale;
        const offX = pad + (W - pad * 2 - drawW) / 2;
        const offY = pad + (H - pad * 2 - drawH) / 2;

        const cx = x => offX + (x - minX) * scale;
        const cy = y => H - offY - (y - minY) * scale;

        // Grid axes through origin
        ctx.strokeStyle = '#30363d';
        ctx.lineWidth = 1;
        ctx.setLineDash([4, 4]);
        const ox = cx(0), oy = cy(0);
        ctx.beginPath(); ctx.moveTo(ox, 0); ctx.lineTo(ox, H); ctx.stroke();
        ctx.beginPath(); ctx.moveTo(0, oy); ctx.lineTo(W, oy); ctx.stroke();
        ctx.setLineDash([]);

        // Path line
        if (path.length >= 2) {
            ctx.strokeStyle = '#1f6feb';
            ctx.lineWidth = 2;
            ctx.lineJoin = 'round';
            ctx.beginPath();
            ctx.moveTo(cx(path[0][0]), cy(path[0][1]));
            for (let i = 1; i < path.length; i++) {
                ctx.lineTo(cx(path[i][0]), cy(path[i][1]));
            }
            ctx.stroke();
        }

        // Origin dot (green)
        ctx.fillStyle = '#3fb950';
        ctx.beginPath();
        ctx.arc(cx(0), cy(0), 5, 0, Math.PI * 2);
        ctx.fill();

        // Current position dot (white)
        if (path.length > 0) {
            const last = path[path.length - 1];
            ctx.fillStyle = '#ffffff';
            ctx.beginPath();
            ctx.arc(cx(last[0]), cy(last[1]), 4, 0, Math.PI * 2);
            ctx.fill();
        }

        // Scale label
        const meterPx = scale;
        const labelM = meterPx > 60 ? 0.1 : (meterPx > 20 ? 0.5 : 1.0);
        ctx.strokeStyle = '#6e7681';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(W - pad - labelM * scale, H - 8);
        ctx.lineTo(W - pad, H - 8);
        ctx.stroke();
        ctx.fillStyle = '#6e7681';
        ctx.font = '9px monospace';
        ctx.textAlign = 'right';
        ctx.fillText(labelM + 'm', W - pad, H - 10);
        ctx.textAlign = 'left';
    }

    // Draw empty canvas on load
    drawPath([]);

    // ── Status Polling ────────────────────────────────────────────────────
    function pollStatus() {
        fetch('/status')
            .then(r => r.json())
            .then(data => {
                document.getElementById('pose-x').textContent = data.pose.x.toFixed(3);
                document.getElementById('pose-y').textContent = data.pose.y.toFixed(3);
                document.getElementById('pose-theta').textContent = data.pose.theta_deg.toFixed(1);

                let msg = data.message;
                if (data.maneuver !== 'idle') {
                    msg = '[' + data.maneuver.toUpperCase() + '] ' + msg;
                }
                if (data.pid_error_deg !== 0) {
                    msg += '  |  err: ' + data.pid_error_deg.toFixed(1) + '\u00b0';
                }
                document.getElementById('maneuver-status').textContent = msg;

                drawPath(data.path);
            })
            .catch(() => {});
    }

    setInterval(pollStatus, 400);
    pollStatus();

    // ── Maneuver Controls ─────────────────────────────────────────────────
    function runManeuver(type) {
        let value;
        if (type === 'straight') value = parseFloat(document.getElementById('straight-dist').value);
        else if (type === 'turn')   value = parseFloat(document.getElementById('turn-deg').value);
        else if (type === 'square') value = parseFloat(document.getElementById('square-side').value);

        postJSON('/maneuver', {type: type, value: value})
            .then(() => showStatus('maneuver-msg', type + ' started', 'success'))
            .catch(() => showStatus('maneuver-msg', 'Failed to start', 'error'));
    }

    function stopManeuver() {
        postJSON('/stop', {})
            .then(() => showStatus('maneuver-msg', 'Stopped', 'success'))
            .catch(() => showStatus('maneuver-msg', 'Error', 'error'));
    }

    function resetPose() {
        postJSON('/reset_pose', {})
            .then(() => showStatus('maneuver-msg', 'Pose reset', 'success'))
            .catch(() => showStatus('maneuver-msg', 'Error', 'error'));
    }

    function resetSim() {
        postJSON('/reset_sim', {})
            .then(() => showStatus('maneuver-msg', 'Simulation reset', 'success'))
            .catch(() => showStatus('maneuver-msg', 'Error', 'error'));
    }

    // ── PID / Robot Controls ──────────────────────────────────────────────
    function applyPID() {
        postJSON('/update_pid', {
            K_P: parseFloat(document.getElementById('pid-kp').value),
            K_I: parseFloat(document.getElementById('pid-ki').value),
            K_D: parseFloat(document.getElementById('pid-kd').value),
            v_0: parseFloat(document.getElementById('robot-v0').value),
        }).then(() => showStatus('pid-status', 'Updated!', 'success'))
          .catch(() => showStatus('pid-status', 'Failed', 'error'));
    }

    function saveCalibration() {
        postJSON('/save_calibration', {})
            .then(() => {
                showStatus('robot-status', 'Calibration saved!', 'success');
                const cs = document.getElementById('calib-status');
                if (cs) showStatus('calib-status', 'Saved!', 'success');
            })
            .catch(() => showStatus('robot-status', 'Save failed', 'error'));
    }

    // ── Wheel Calibration (real server only) ─────────────────────────────
    function adjustTrim(delta) {
        const input = document.getElementById('calib-trim');
        if (!input) return;
        const newVal = Math.round((parseFloat(input.value || 0) + delta) * 1000) / 1000;
        input.value = newVal.toFixed(3);
        postJSON('/update_calibration', {
            trim: newVal,
            gain: parseFloat(document.getElementById('calib-gain').value),
        }).then(() => showStatus('calib-status', 'Trim: ' + newVal.toFixed(3), 'success'))
          .catch(() => showStatus('calib-status', 'Update failed', 'error'));
    }

    function testDrive(type, duration) {
        postJSON('/test_drive', {type: type, duration: duration})
            .then(() => showStatus('calib-status', 'Running\u2026', 'success'))
            .catch(() => showStatus('calib-status', 'Failed', 'error'));
    }
'''

MODCON_TEMPLATE = render_template('{{ title }}', '{{ subtitle }}', _CONTENT, extra_js=_JS)
