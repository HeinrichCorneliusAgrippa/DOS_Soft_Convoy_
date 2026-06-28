BASE_CSS = '''
* { margin: 0; padding: 0; box-sizing: border-box; }

:root {
    --bg-dark: #1a1d23;
    --bg-darker: #13161a;
    --bg-sidebar: #0d1117;
    --border-color: #30363d;
    --text-primary: #e6edf3;
    --text-secondary: #8b949e;
    --text-muted: #6e7681;
    --accent-blue: #1f6feb;
    --accent-blue-hover: #388bfd;
    --accent-green: #3fb950;
    --accent-red: #f85149;
    --accent-purple: #a371f7;
    --accent-orange: #d29922;
}

body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
    background: var(--bg-dark);
    color: var(--text-primary);
    padding: 12px;
    min-height: 100vh;
}

.header {
    text-align: center;
    margin-bottom: 12px;
    padding-bottom: 12px;
    border-bottom: 1px solid var(--border-color);
}

h1 { font-size: 24px; font-weight: 600; margin-bottom: 6px; }
.subtitle { color: var(--text-secondary); font-size: 13px; }

.container {
    display: grid;
    grid-template-columns: 1fr 360px;
    gap: 12px;
    max-width: 1600px;
    margin: 0 auto;
    align-items: stretch;
    height: calc(100vh - 90px);
}

.video-section {
    background: var(--bg-darker);
    border: 1px solid var(--border-color);
    border-radius: 6px;
    padding: 12px;
    display: flex;
    align-items: center;
    justify-content: center;
    min-height: 0;
    overflow: hidden;
}

.stream {
    width: 100%;
    height: 100%;
    border-radius: 4px;
    display: block;
    object-fit: contain;
}

.controls-section { display: flex; flex-direction: column; gap: 12px; min-height: 0; overflow-y: auto; }

.card {
    background: var(--bg-darker);
    border: 1px solid var(--border-color);
    border-radius: 6px;
    padding: 12px;
}

.card-header {
    font-size: 14px;
    font-weight: 600;
    margin-bottom: 12px;
    padding-bottom: 8px;
    border-bottom: 1px solid var(--border-color);
    display: flex;
    justify-content: space-between;
    align-items: center;
}

/* Stats boxes */
.stats-grid {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
    margin-bottom: 12px;
}

.stat-box {
    background: var(--bg-sidebar);
    border: 1px solid var(--border-color);
    border-radius: 4px;
    padding: 8px;
    text-align: center;
}

.stat-value {
    font-size: 20px;
    font-weight: 600;
    color: var(--accent-blue);
}

.stat-label {
    font-size: 10px;
    color: var(--text-muted);
    text-transform: uppercase;
    margin-top: 3px;
}

/* Sliders */
.slider-group { margin-bottom: 12px; }

.slider-label {
    display: flex;
    justify-content: space-between;
    margin-bottom: 6px;
    font-size: 12px;
    color: var(--text-secondary);
}

.slider-controls { display: flex; gap: 6px; align-items: center; }

.slider {
    flex: 1;
    height: 5px;
    background: var(--bg-sidebar);
    outline: none;
    border-radius: 3px;
    appearance: none;
}

.slider::-webkit-slider-thumb {
    appearance: none;
    width: 14px;
    height: 14px;
    background: var(--accent-blue);
    cursor: pointer;
    border-radius: 50%;
    border: 2px solid var(--bg-darker);
}

.input-box {
    width: 55px;
    padding: 5px 6px;
    background: var(--bg-sidebar);
    border: 1px solid var(--border-color);
    border-radius: 4px;
    color: var(--text-primary);
    font-size: 12px;
    text-align: center;
}

.input-box:focus { outline: none; border-color: var(--accent-blue); }

/* Buttons */
.button {
    width: 100%;
    padding: 8px 12px;
    background: var(--accent-blue);
    color: white;
    border: none;
    border-radius: 5px;
    font-size: 13px;
    font-weight: 500;
    cursor: pointer;
    transition: background 0.15s;
    font-family: 'Inter', sans-serif;
    margin-bottom: 6px;
}

.button:hover { background: var(--accent-blue-hover); }
.button.success { background: var(--accent-green); }
.button.success:hover { background: #2ea043; }
.button.danger { background: var(--accent-red); }
.button.danger:hover { background: #da3633; }

/* Status messages */
.status {
    text-align: center;
    padding: 6px;
    margin-top: 8px;
    border-radius: 4px;
    font-size: 12px;
    min-height: 28px;
}

.status.success {
    background: rgba(63, 185, 80, 0.1);
    border: 1px solid rgba(63, 185, 80, 0.3);
    color: var(--accent-green);
}

.status.error {
    background: rgba(248, 81, 73, 0.1);
    border: 1px solid rgba(248, 81, 73, 0.3);
    color: var(--accent-red);
}

/* Config items */
.config-item {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 0;
    border-bottom: 1px solid var(--border-color);
}

.config-item:last-child { border-bottom: none; }

.config-label {
    color: var(--text-secondary);
    font-size: 13px;
}

.config-value {
    color: var(--text-primary);
    font-weight: 500;
}

/* Responsive */
@media (max-width: 1200px) {
    .container { grid-template-columns: 1fr; }
}
'''

BASE_JS = '''
function showStatus(elementId, message, type) {
    const status = document.getElementById(elementId);
    status.textContent = message;
    status.className = 'status ' + type;
    setTimeout(() => {
        status.textContent = '';
        status.className = 'status';
    }, 2000);
}

function syncSliderInput(sliderId, callback) {
    const slider = document.getElementById(sliderId);
    const input = document.getElementById(sliderId + '-input');
    let timeout = null;

    slider.addEventListener('input', function() {
        input.value = this.value;
        clearTimeout(timeout);
        timeout = setTimeout(callback, 200);
    });

    input.addEventListener('input', function() {
        let val = parseInt(this.value);
        val = Math.max(parseInt(this.min), Math.min(parseInt(this.max), val));
        this.value = val;
        slider.value = val;
        clearTimeout(timeout);
        timeout = setTimeout(callback, 200);
    });
}

function setSliderValue(id, value) {
    document.getElementById(id).value = value;
    document.getElementById(id + '-input').value = value;
}

function postJSON(url, data) {
    return fetch(url, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data)
    }).then(response => response.json());
}
'''


def render_template(title, subtitle, content_html, extra_css='', extra_js=''):
    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
{BASE_CSS}
{extra_css}
    </style>
</head>
<body>
    <div class="header">
        <h1>{title}</h1>
        <p class="subtitle">{subtitle}</p>
    </div>
    {content_html}
    <script>
{BASE_JS}
{extra_js}
    </script>
</body>
</html>'''
