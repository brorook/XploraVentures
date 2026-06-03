"""
Accelerated Reactor Dashboard
Flask + SocketIO dashboard for the AcceleratedReactor firmware.
Two SHT45 sensors (Ch1, Ch3), coil heater on MOSFET CH0, solenoid on CH1.
"""

import serial
import serial.tools.list_ports
import threading
import json
import csv
import os
import datetime
import webbrowser

from flask import Flask, request, jsonify, send_file
from flask_socketio import SocketIO

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*")

_ser       = None
_ser_lock  = threading.Lock()
_rx_thread = None
_log_file  = None
_log_writer= None
_log_path  = None
_log_lock  = threading.Lock()
# ─── Serial ───────────────────────────────────────────────────────────────────

def _serial_reader():
    global _ser
    while True:
        with _ser_lock:
            s = _ser
        if s is None:
            break
        try:
            line = s.readline().decode("utf-8", errors="ignore").strip()
        except Exception:
            break
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        socketio.emit("telemetry", data)
        _log_row(data)

def _send(obj):
    with _ser_lock:
        if _ser and _ser.is_open:
            _ser.write((json.dumps(obj) + "\n").encode())

# ─── CSV Logging ──────────────────────────────────────────────────────────────

def _log_row(data):
    with _log_lock:
        if _log_writer is None:
            return
        _log_writer.writerow([
            datetime.datetime.now().isoformat(),
            data.get("sht1", {}).get("t", ""),
            data.get("sht1", {}).get("h", ""),
            data.get("sht3", {}).get("t", ""),
            data.get("sht3", {}).get("h", ""),
            int(data.get("heater",   False)),
            int(data.get("solenoid", False)),
            data.get("setpoint", ""),
            data.get("pid_pct", ""),
        ])
        _log_file.flush()

# ─── API ──────────────────────────────────────────────────────────────────────

@app.route("/api/ports")
def api_ports():
    ports = [p.device for p in serial.tools.list_ports.comports()]
    return jsonify(ports)

@app.route("/api/connect", methods=["POST"])
def api_connect():
    global _ser, _rx_thread
    body = request.json or {}
    port = body.get("port", "")
    baud = int(body.get("baud", 115200))
    with _ser_lock:
        if _ser and _ser.is_open:
            _ser.close()
        try:
            _ser = serial.Serial(port, baud, timeout=1)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)})
    _rx_thread = threading.Thread(target=_serial_reader, daemon=True)
    _rx_thread.start()
    return jsonify({"ok": True})

@app.route("/api/disconnect", methods=["POST"])
def api_disconnect():
    global _ser
    with _ser_lock:
        if _ser:
            _ser.close()
            _ser = None
    return jsonify({"ok": True})

@app.route("/api/command", methods=["POST"])
def api_command():
    _send(request.json or {})
    return jsonify({"ok": True})

@app.route("/api/log/start", methods=["POST"])
def api_log_start():
    global _log_file, _log_writer, _log_path
    with _log_lock:
        if _log_writer:
            return jsonify({"ok": False, "error": "already logging"})
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        _log_path = f"accel_reactor_{ts}.csv"
        _log_file = open(_log_path, "w", newline="")
        _log_writer = csv.writer(_log_file)
        _log_writer.writerow(["timestamp","ch1_t","ch1_h","ch3_t","ch3_h","heater","solenoid","setpoint","pid_pct"])
    return jsonify({"ok": True, "file": _log_path})

@app.route("/api/log/stop", methods=["POST"])
def api_log_stop():
    global _log_file, _log_writer, _log_path
    with _log_lock:
        if _log_file:
            _log_file.close()
        _log_file = _log_writer = None
    return jsonify({"ok": True})

@app.route("/api/ip")
def api_ip():
    import socket as _socket
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:
        ip = "127.0.0.1"
    return jsonify({"ip": ip, "port": 8080})

@app.route("/api/log/download")
def api_log_download():
    if _log_path and os.path.exists(_log_path):
        return send_file(_log_path, as_attachment=True)
    return jsonify({"error": "no log"}), 404

# ─── HTML ─────────────────────────────────────────────────────────────────────

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Accelerated Reactor</title>
<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.3/dist/chart.umd.min.js"></script>
<style>
  *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
  body { background: #0f1117; color: #e0e0e0; font-family: 'Segoe UI', sans-serif; min-height: 100vh; }

  header {
    background: #1a1d27; border-bottom: 1px solid #2a2d3a;
    padding: 14px 24px; display: flex; align-items: center; gap: 16px;
  }
  header h1 { font-size: 1.1rem; font-weight: 600; color: #fff; }
  .badge { background: #252836; border: 1px solid #3a3d4a; border-radius: 4px;
           padding: 2px 8px; font-size: 0.72rem; color: #9ba3af; }
  .dot { width: 10px; height: 10px; border-radius: 50%; background: #4b5563; margin-left: auto; }
  .dot.connected { background: #10b981; }

  nav { background: #1a1d27; border-bottom: 1px solid #2a2d3a; padding: 0 24px; display: flex; }
  nav button {
    background: none; border: none; color: #9ba3af; padding: 12px 18px;
    font-size: 0.85rem; cursor: pointer; border-bottom: 2px solid transparent;
  }
  nav button.active { color: #fff; border-bottom-color: #3b82f6; }

  .tab { display: none; padding: 24px; max-width: 1100px; margin: 0 auto; }
  .tab.active { display: block; }

  .grid { display: grid; gap: 16px; }
  .grid-2 { grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); }
  .grid-3 { grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); }

  .card {
    background: #1a1d27; border: 1px solid #2a2d3a; border-radius: 10px; padding: 18px;
  }
  .card h3 { font-size: 0.8rem; color: #6b7280; text-transform: uppercase;
             letter-spacing: 0.05em; margin-bottom: 12px; }

  .sensor-val { font-size: 2.4rem; font-weight: 700; color: #f3f4f6; line-height: 1; }
  .sensor-sub { font-size: 0.85rem; color: #9ba3af; margin-top: 6px; }
  .sensor-unit { font-size: 1rem; color: #6b7280; }

  .row { display: flex; align-items: center; gap: 10px; margin-bottom: 10px; }
  .row:last-child { margin-bottom: 0; }

  label { font-size: 0.82rem; color: #9ba3af; min-width: 90px; }
  select, input[type=number], input[type=text] {
    background: #252836; border: 1px solid #3a3d4a; border-radius: 6px;
    color: #e0e0e0; padding: 6px 10px; font-size: 0.85rem; flex: 1;
  }
  select:focus, input:focus { outline: none; border-color: #3b82f6; }

  button.btn {
    background: #3b82f6; border: none; border-radius: 6px; color: #fff;
    padding: 7px 16px; font-size: 0.85rem; cursor: pointer; white-space: nowrap;
  }
  button.btn:hover { background: #2563eb; }
  button.btn.danger { background: #ef4444; }
  button.btn.danger:hover { background: #dc2626; }
  button.btn.success { background: #10b981; }
  button.btn.success:hover { background: #059669; }
  button.btn.ghost {
    background: #252836; border: 1px solid #3a3d4a; color: #e0e0e0;
  }
  button.btn.ghost:hover { background: #2f3340; }

  .status-pill {
    display: inline-flex; align-items: center; gap: 6px;
    background: #252836; border: 1px solid #3a3d4a; border-radius: 20px;
    padding: 4px 12px; font-size: 0.8rem;
  }
  .status-pill .dot { margin-left: 0; }
  .status-pill.on .dot { background: #10b981; }
  .status-pill.off .dot { background: #4b5563; }

  .divider { border: none; border-top: 1px solid #2a2d3a; margin: 12px 0; }
</style>
</head>
<body>

<header>
  <div style="display:flex; flex-direction:column; gap:2px;">
    <span id="ipBadge" style="font-size:0.68rem; color:#6b7280; letter-spacing:0.03em;"></span>
    <h1>XploraVentures</h1>
  </div>
  <span class="badge">Accelerated Reactor</span>
  <span id="fwBadge" class="badge" style="display:none"></span>
  <div id="connDot" class="dot" title="Serial connection"></div>
</header>

<nav>
  <button class="active" onclick="showTab('reactor')">Accelerated Reactor</button>
</nav>

<!-- ── Accelerated Reactor Tab ──────────────────────────────────────────── -->
<div id="tab-reactor" class="tab active">

  <!-- Connection -->
  <div class="card" style="margin-bottom:16px">
    <h3>Serial Connection</h3>
    <div class="row">
      <label>Port</label>
      <select id="portSel"></select>
      <button class="btn ghost" onclick="refreshPorts()">Refresh</button>
    </div>
    <div class="row">
      <label>Baud</label>
      <select id="baudSel">
        <option value="115200" selected>115200</option>
        <option value="9600">9600</option>
      </select>
    </div>
    <div class="row" style="margin-top:4px">
      <button class="btn" id="connectBtn" onclick="connect()">Connect</button>
      <button class="btn ghost" onclick="disconnect()">Disconnect</button>
    </div>
  </div>

  <!-- Sensors -->
  <div class="grid grid-2" style="margin-bottom:16px">
    <!-- Channel 1 -->
    <div class="card">
      <h3>Channel 1 — Temp / Humidity</h3>
      <div class="sensor-val" id="ch1T">--<span class="sensor-unit"> °C</span></div>
      <div class="sensor-sub" id="ch1H">Humidity: -- %</div>
    </div>
    <!-- Channel 3 -->
    <div class="card">
      <h3>Channel 3 — Temp / Humidity <span style="color:#f59e0b;font-size:0.7rem">(heater sensor)</span></h3>
      <div class="sensor-val" id="ch3T">--<span class="sensor-unit"> °C</span></div>
      <div class="sensor-sub" id="ch3H">Humidity: -- %</div>
    </div>
  </div>

  <!-- Controls -->
  <div class="grid grid-2" style="margin-bottom:16px">
    <!-- Heater -->
    <div class="card">
      <h3>Coil Heater — MOSFET CH0</h3>
      <div class="row" style="margin-bottom:8px">
        <span id="heaterPill" class="status-pill off"><span class="dot"></span> OFF</span>
        <span id="dutyBadge" style="margin-left:10px;font-size:0.82rem;color:#f59e0b">Duty: --%</span>
      </div>
      <div style="background:#252836;border-radius:4px;height:6px;margin-bottom:14px">
        <div id="dutyBar" style="background:#f59e0b;border-radius:4px;height:6px;width:0%;transition:width 0.4s"></div>
      </div>
      <hr class="divider">
      <div class="row">
        <label>Setpoint (°C)</label>
        <input type="number" id="spInput" value="30" step="0.5" min="-40" max="150">
        <button class="btn" onclick="sendSetpoint()">Set</button>
      </div>
    </div>

    <!-- Solenoid -->
    <div class="card">
      <h3>Solenoid — MOSFET CH1</h3>
      <div class="row" style="margin-bottom:14px">
        <span id="solenoidPill" class="status-pill off"><span class="dot"></span> OFF</span>
      </div>
      <hr class="divider">
      <div class="row">
        <button class="btn success" onclick="setSolenoid(true)">Open</button>
        <button class="btn danger"  onclick="setSolenoid(false)">Close</button>
      </div>
    </div>
  </div>

  <!-- PID Tuning -->
  <div class="card" style="margin-bottom:16px">
    <h3>PID Tuning</h3>
    <div class="grid grid-3">
      <div class="row">
        <label>Kp</label>
        <input type="number" id="pidKp" value="15" step="0.5" min="0">
      </div>
      <div class="row">
        <label>Ki</label>
        <input type="number" id="pidKi" value="0.119" step="0.001" min="0">
      </div>
      <div class="row">
        <label>Kd</label>
        <input type="number" id="pidKd" value="0" step="0.1" min="0">
      </div>
    </div>
    <div class="row" style="margin-top:10px">
      <button class="btn" onclick="sendPid()">Apply</button>
      <span style="font-size:0.78rem;color:#6b7280;margin-left:10px">Changes take effect immediately. Values echo back in telemetry.</span>
    </div>
  </div>

  <!-- Logging -->
  <div class="card" style="margin-bottom:16px">
    <h3>PC Logging</h3>
    <div class="row">
      <button class="btn" id="logStartBtn" onclick="logStart()">Start Log</button>
      <button class="btn ghost" onclick="logStop()">Stop</button>
      <button class="btn ghost" onclick="window.open('/api/log/download')">Download CSV</button>
      <span id="logStatus" style="font-size:0.8rem;color:#6b7280;margin-left:8px"></span>
    </div>
  </div>

  <!-- Graph -->
  <div class="card">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
      <h3 style="margin:0">Live History</h3>
      <button class="btn ghost" onclick="clearChart()" style="padding:4px 10px;font-size:0.75rem">Clear</button>
    </div>
    <div style="position:relative;height:280px">
      <canvas id="liveChart"></canvas>
    </div>
  </div>

</div>

<script>
fetch("/api/ip").then(r => r.json()).then(d => {
  document.getElementById("ipBadge").textContent = d.ip + ":" + d.port;
});

const socket = io();
let connected = false;
const MAX_POINTS = 300;  // ~10 min at 2 s/sample

// ── Chart setup ──────────────────────────────────────────────────────────────
const ctx = document.getElementById("liveChart").getContext("2d");
const chart = new Chart(ctx, {
  type: "line",
  data: {
    labels: [],
    datasets: [
      { label: "Ch1 Temp (°C)",  yAxisID: "yT", data: [], borderColor: "#3b82f6", backgroundColor: "transparent", pointRadius: 0, borderWidth: 1.5, tension: 0.3 },
      { label: "Ch3 Temp (°C)",  yAxisID: "yT", data: [], borderColor: "#f59e0b", backgroundColor: "transparent", pointRadius: 0, borderWidth: 1.5, tension: 0.3 },
      { label: "Setpoint (°C)",  yAxisID: "yT", data: [], borderColor: "#ef4444", backgroundColor: "transparent", pointRadius: 0, borderWidth: 1.5, borderDash: [6,3], tension: 0 },
      { label: "Ch1 Humidity (%)", yAxisID: "yH", data: [], borderColor: "#60a5fa", backgroundColor: "transparent", pointRadius: 0, borderWidth: 1, borderDash: [3,3], tension: 0.3 },
      { label: "Ch3 Humidity (%)", yAxisID: "yH", data: [], borderColor: "#fbbf24", backgroundColor: "transparent", pointRadius: 0, borderWidth: 1, borderDash: [3,3], tension: 0.3 },
      { label: "PID Duty (%)",     yAxisID: "yH", data: [], borderColor: "#a78bfa", backgroundColor: "transparent", pointRadius: 0, borderWidth: 1.5, tension: 0.3 },
    ]
  },
  options: {
    animation: false,
    responsive: true,
    maintainAspectRatio: false,
    interaction: { mode: "index", intersect: false },
    plugins: {
      legend: { labels: { color: "#9ba3af", boxWidth: 24, font: { size: 11 } } },
      tooltip: { backgroundColor: "#1a1d27", borderColor: "#3a3d4a", borderWidth: 1, titleColor: "#e0e0e0", bodyColor: "#9ba3af" }
    },
    scales: {
      x: { ticks: { color: "#6b7280", maxTicksLimit: 8, font: { size: 10 } }, grid: { color: "#2a2d3a" } },
      yT: {
        position: "left", title: { display: true, text: "Temperature (°C)", color: "#9ba3af", font: { size: 11 } },
        ticks: { color: "#9ba3af", font: { size: 10 } }, grid: { color: "#2a2d3a" }
      },
      yH: {
        position: "right", title: { display: true, text: "Humidity (%)", color: "#9ba3af", font: { size: 11 } },
        ticks: { color: "#9ba3af", font: { size: 10 } }, grid: { drawOnChartArea: false },
        min: 0, max: 100
      }
    }
  }
});

function pushChart(t1, h1, t3, h3, sp, duty) {
  const ts = new Date().toLocaleTimeString();
  const ds = chart.data.datasets;
  const labels = chart.data.labels;
  labels.push(ts);
  ds[0].data.push(t1);
  ds[1].data.push(t3);
  ds[2].data.push(sp);
  ds[3].data.push(h1);
  ds[4].data.push(h3);
  ds[5].data.push(duty ?? null);
  if (labels.length > MAX_POINTS) {
    labels.shift();
    ds.forEach(d => d.data.shift());
  }
  chart.update("none");
}

function clearChart() {
  chart.data.labels = [];
  chart.data.datasets.forEach(d => d.data = []);
  chart.update("none");
}

// ── Telemetry ─────────────────────────────────────────────────────────────────
socket.on("telemetry", d => {
  // Channel 1
  if (d.sht1 !== undefined) {
    document.getElementById("ch1T").innerHTML = d.sht1.t.toFixed(1) + '<span class="sensor-unit"> °C</span>';
    document.getElementById("ch1H").textContent = "Humidity: " + d.sht1.h.toFixed(1) + " %";
  }
  // Channel 3
  if (d.sht3 !== undefined) {
    document.getElementById("ch3T").innerHTML = d.sht3.t.toFixed(1) + '<span class="sensor-unit"> °C</span>';
    document.getElementById("ch3H").textContent = "Humidity: " + d.sht3.h.toFixed(1) + " %";
  }
  // Heater + duty
  if (d.heater !== undefined) {
    const p = document.getElementById("heaterPill");
    p.className = "status-pill " + (d.heater ? "on" : "off");
    p.innerHTML = '<span class="dot"></span> ' + (d.heater ? "ON" : "OFF");
  }
  if (d.pid_pct !== undefined) {
    document.getElementById("dutyBadge").textContent = "Duty: " + d.pid_pct + "%";
    document.getElementById("dutyBar").style.width = d.pid_pct + "%";
  }
  // PID tunings (populate inputs once, only when not focused)
  if (d.pid) {
    const focused = document.activeElement;
    const kpEl = document.getElementById("pidKp");
    const kiEl = document.getElementById("pidKi");
    const kdEl = document.getElementById("pidKd");
    if (kpEl !== focused) kpEl.value = d.pid.kp;
    if (kiEl !== focused) kiEl.value = d.pid.ki;
    if (kdEl !== focused) kdEl.value = d.pid.kd;
  }
  // Solenoid
  if (d.solenoid !== undefined) {
    const p = document.getElementById("solenoidPill");
    p.className = "status-pill " + (d.solenoid ? "on" : "off");
    p.innerHTML = '<span class="dot"></span> ' + (d.solenoid ? "OPEN" : "CLOSED");
  }
  // Setpoint
  const sp = d.setpoint ?? parseFloat(document.getElementById("spInput").value);
  if (d.setpoint !== undefined && document.getElementById("spInput") !== document.activeElement)
    document.getElementById("spInput").value = d.setpoint;
  // FW
  if (d.fw) {
    const b = document.getElementById("fwBadge");
    b.textContent = "FW " + d.fw; b.style.display = "";
  }
  // Push to chart whenever we have both sensors
  if (d.sht1 !== undefined && d.sht3 !== undefined)
    pushChart(d.sht1.t, d.sht1.h, d.sht3.t, d.sht3.h, sp, d.pid_pct);
});

async function refreshPorts() {
  const r = await fetch("/api/ports"); const ports = await r.json();
  const sel = document.getElementById("portSel");
  sel.innerHTML = ports.map(p => `<option value="${p}">${p}</option>`).join("") || '<option value="">No ports</option>';
}

async function connect() {
  const port = document.getElementById("portSel").value;
  const baud = document.getElementById("baudSel").value;
  const r = await fetch("/api/connect", { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify({port, baud}) });
  const d = await r.json();
  if (d.ok) {
    connected = true;
    document.getElementById("connDot").className = "dot connected";
  } else {
    alert("Connect failed: " + d.error);
  }
}

async function disconnect() {
  await fetch("/api/disconnect", { method:"POST" });
  connected = false;
  document.getElementById("connDot").className = "dot";
}

async function sendCmd(obj) {
  await fetch("/api/command", { method:"POST", headers:{"Content-Type":"application/json"}, body: JSON.stringify(obj) });
}

function sendSetpoint() {
  const v = parseFloat(document.getElementById("spInput").value);
  if (!isNaN(v)) sendCmd({ cmd: "set_sp", val: v });
}

function setSolenoid(on) { sendCmd({ cmd: "solenoid", on }); }

function sendPid() {
  const kp = parseFloat(document.getElementById("pidKp").value);
  const ki = parseFloat(document.getElementById("pidKi").value);
  const kd = parseFloat(document.getElementById("pidKd").value);
  if ([kp, ki, kd].some(isNaN)) return;
  sendCmd({ cmd: "set_pid", kp, ki, kd });
}

async function logStart() {
  const r = await fetch("/api/log/start", { method:"POST" });
  const d = await r.json();
  if (d.ok) document.getElementById("logStatus").textContent = "Logging: " + d.file;
}

async function logStop() {
  await fetch("/api/log/stop", { method:"POST" });
  document.getElementById("logStatus").textContent = "Stopped.";
}

refreshPorts();
</script>
</body>
</html>
"""

@app.route("/")
def index():
    return HTML

# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    webbrowser.open("http://localhost:8080")
    socketio.run(app, host="0.0.0.0", port=8080, debug=False, allow_unsafe_werkzeug=True)
