#!/usr/bin/env python3
"""
XploraVentures Dashboard
━━━━━━━━━━━━━━━━━━━━━━━━
ESP32 → PC  (newline-delimited JSON):
{
  "sht":    [{"t": 23.5, "h": 45.2}, ...],   // up to 16 entries (2 boards × 8)
  "pt1000": [{"t": 105.3}, ...],              // up to  8 entries (2 boards × 4)
  "batt":   {"v": 3.85, "soc": 78.5},
  "mosfet": [false, true, false, false],
  "kcs208": {"pv": 150, "sv": 160, "mv": 75, "run": true, "status": 0}
}

PC → ESP32  (newline-delimited JSON commands):
  {"cmd": "mosfet",     "ch": 0, "on": true}
  {"cmd": "kcs208_sv",  "val": 160}
  {"cmd": "kcs208_run", "run": true}
  {"cmd": "wifi",       "ssid": "MyNet", "pass": "secret"}
  {"cmd": "sd_log",     "active": true}
"""

import csv
import json
import socket
import threading
import webbrowser
from datetime import datetime
from pathlib import Path

import serial
import serial.tools.list_ports
from flask import Flask, jsonify, render_template_string, request, send_file
from flask_socketio import SocketIO

app = Flask(__name__)
app.config['SECRET_KEY'] = 'xplora-secret'
socketio = SocketIO(app, cors_allowed_origins='*', async_mode='threading')

_ser = None
_ser_lock = threading.Lock()
_latest = {}
_log_active = False
_log_path = None
_log_fh = None
_log_writer = None

N_SHT = 16
N_PT  = 8

# ──────────────────────────────────────────────────────────────────────────────
_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>XploraVentures</title>
<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg:      #0f1117;
  --surface: #1a1d27;
  --border:  #2a2d3a;
  --accent:  #00d4aa;
  --text:    #e2e8f0;
  --muted:   #64748b;
  --warn:    #f59e0b;
  --danger:  #ef4444;
  --ok:      #10b981;
}
body { background: var(--bg); color: var(--text); font-family: system-ui, sans-serif; font-size: 14px; }

header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 24px; height: 56px;
  background: var(--surface); border-bottom: 1px solid var(--border);
  position: sticky; top: 0; z-index: 10;
}
header h1 { font-size: 18px; font-weight: 700; color: var(--accent); }
.hdr-right { display: flex; align-items: center; gap: 16px; }
#batt-hdr { display: flex; align-items: center; gap: 8px; font-size: 13px; }
#batt-hdr-v { color: var(--accent); font-weight: 700; min-width: 60px; }
#batt-hdr-bar { width: 56px; background: var(--border); border-radius: 3px; height: 5px; overflow: hidden; }
#batt-hdr-fill { height: 100%; background: var(--ok); border-radius: 3px; width: 0%; transition: width .6s, background .3s; }
#batt-hdr-soc { color: var(--muted); font-size: 12px; min-width: 42px; }
#sd-hdr { display: flex; align-items: center; gap: 5px; font-size: 12px; color: var(--muted); background: var(--border); padding: 3px 10px; border-radius: 20px; }
#sd-hdr-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); flex-shrink: 0; transition: background .3s; }
#sd-hdr-dot.present { background: var(--ok); }
#sd-hdr-dot.logging { background: var(--danger); animation: blink 1s infinite; }
#conn-status { display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--muted); }
#conn-dot { width: 10px; height: 10px; border-radius: 50%; background: var(--muted); transition: background .3s; }
#conn-dot.on { background: var(--ok); box-shadow: 0 0 6px var(--ok); }
#refresh-display { font-size: 12px; color: var(--muted); background: var(--border); padding: 3px 10px; border-radius: 20px; }
#refresh-display span { color: var(--accent); font-weight: 600; }

main { padding: 20px 24px; display: flex; flex-direction: column; gap: 16px; max-width: 1600px; margin: 0 auto; }

.card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px; }
.card-title { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); margin-bottom: 14px; }

label { font-size: 12px; color: var(--muted); display: block; margin-bottom: 4px; }
select, input[type=text], input[type=password], input[type=number] {
  background: var(--bg); border: 1px solid var(--border); color: var(--text);
  border-radius: 6px; padding: 8px 10px; font-size: 13px; outline: none;
}
select:focus, input:focus { border-color: var(--accent); }
button { padding: 8px 18px; border-radius: 6px; border: none; cursor: pointer; font-size: 13px; font-weight: 600; transition: opacity .15s; }
button:hover { opacity: .82; }
button:disabled { opacity: .35; cursor: default; }
.btn-primary { background: var(--accent);  color: #000; }
.btn-danger  { background: var(--danger);  color: #fff; }
.btn-warn    { background: var(--warn);    color: #000; }
.btn-neutral { background: var(--border);  color: var(--text); }
.row { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; }

/* Sensor grids */
.sht-row { display: grid; grid-template-columns: repeat(8, 1fr); gap: 10px; }
.pt-row  { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
.board-row-label { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 1px; color: var(--muted); margin: 10px 0 6px; }
.board-row-label:first-of-type { margin-top: 0; }
.grid-4  { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 10px; }
.grid-2  { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 16px; }
.grid-charts { display: grid; grid-template-columns: repeat(auto-fill, minmax(500px, 1fr)); gap: 16px; }

.sensor-card { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
.sensor-label { font-size: 10px; color: var(--muted); margin-bottom: 4px; text-transform: uppercase; letter-spacing: .5px; }
.sensor-value { font-size: 22px; font-weight: 700; color: var(--accent); line-height: 1; }
.sensor-value.stale { color: var(--muted); }
.sensor-sub { font-size: 12px; color: var(--muted); margin-top: 3px; }
.board-tag { font-size: 9px; color: var(--muted); background: var(--border); padding: 1px 6px; border-radius: 10px; display: inline-block; margin-bottom: 6px; }

/* Chart cards */
.chart-card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 16px 20px 12px; }
.chart-card-hdr { display: flex; justify-content: space-between; align-items: baseline; margin-bottom: 12px; }
.chart-card-title { font-size: 15px; font-weight: 700; color: var(--text); }
.chart-card-sub { font-size: 11px; color: var(--muted); }
.chart-wrap { position: relative; height: 260px; }


/* MOSFET toggles */
.mosfet-item { display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; background: var(--bg); border: 1px solid var(--border); border-radius: 8px; margin-bottom: 8px; }
.mosfet-item:last-child { margin-bottom: 0; }
.toggle { position: relative; width: 44px; height: 24px; flex-shrink: 0; }
.toggle input { opacity: 0; width: 0; height: 0; }
.toggle-track { position: absolute; inset: 0; background: var(--border); border-radius: 24px; cursor: pointer; transition: background .2s; }
.toggle input:checked + .toggle-track { background: var(--accent); }
.toggle-track::after { content: ''; position: absolute; left: 3px; top: 3px; width: 18px; height: 18px; border-radius: 50%; background: #fff; transition: transform .2s; }
.toggle input:checked + .toggle-track::after { transform: translateX(20px); }

/* KCS208 */
.kcs-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 14px; }
.kcs-item { background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
.kcs-label { font-size: 11px; color: var(--muted); }
.kcs-value { font-size: 22px; font-weight: 700; margin-top: 2px; }
#kcs-badge { display: inline-block; padding: 3px 12px; border-radius: 20px; font-size: 12px; font-weight: 600; background: var(--border); color: var(--muted); }

/* Logging */
.log-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--muted); flex-shrink: 0; }
.log-dot.active { background: var(--danger); animation: blink 1s infinite; }
@keyframes blink { 0%,100%{opacity:1} 50%{opacity:.3} }

#toast { position: fixed; bottom: 24px; right: 24px; background: var(--surface); border: 1px solid var(--accent); padding: 12px 18px; border-radius: 8px; font-size: 13px; opacity: 0; transition: opacity .25s; pointer-events: none; max-width: 320px; }
#toast.err { border-color: var(--danger); }
#toast.show { opacity: 1; }
</style>
</head>
<body>

<header>
  <h1>XploraVentures</h1>
  <div class="hdr-right">
    <div id="sd-hdr">
      <div id="sd-hdr-dot"></div>
      <span id="sd-hdr-label">SD —</span>
    </div>
    <div id="batt-hdr">
      <span id="batt-hdr-v">— V</span>
      <div id="batt-hdr-bar"><div id="batt-hdr-fill"></div></div>
      <span id="batt-hdr-soc">— %</span>
    </div>
    <div id="refresh-display">Refresh <span id="refresh-rate">—</span></div>
    <div id="conn-status">
      <div id="conn-dot"></div>
      <span id="conn-label">Disconnected</span>
    </div>
  </div>
</header>

<main>

  <!-- Connection -->
  <div class="card">
    <div class="card-title">Serial Connection</div>
    <div class="row">
      <div><label>Port</label><select id="sel-port" style="min-width:220px"><option>Loading…</option></select></div>
      <div><label>Baud Rate</label>
        <select id="sel-baud">
          <option value="115200" selected>115200</option>
          <option value="921600">921600</option>
          <option value="9600">9600</option>
        </select>
      </div>
      <button class="btn-primary" id="btn-connect"  style="margin-top:18px">Connect</button>
      <button class="btn-neutral" id="btn-ports"    style="margin-top:18px">Refresh Ports</button>
    </div>
  </div>

  <!-- SHT45 sensors -->
  <div class="card">
    <div class="card-title">Temperature &amp; Humidity — SHT45 (up to ×16)</div>
    <div class="board-row-label">Board 1</div>
    <div class="sht-row" id="sht-grid-b1"></div>
    <div class="board-row-label">Board 2</div>
    <div class="sht-row" id="sht-grid-b2"></div>
  </div>

  <!-- PT1000 -->
  <div class="card">
    <div class="card-title">PT1000 RTD (up to ×8)</div>
    <div class="board-row-label">Board 1</div>
    <div class="pt-row" id="pt-grid-b1"></div>
    <div class="board-row-label" style="margin-top:14px;padding-top:14px;border-top:1px solid var(--border)">Board 2</div>
    <div class="pt-row" id="pt-grid-b2"></div>
  </div>

  <!-- Charts: full width stacked -->
  <div class="chart-card">
    <div class="chart-card-hdr">
      <span class="chart-card-title">SHT45 — Temperature</span>
      <span class="chart-card-sub">Last 60 readings</span>
    </div>
    <div class="chart-wrap"><canvas id="chart-sht-t"></canvas></div>
  </div>
  <div class="chart-card">
    <div class="chart-card-hdr">
      <span class="chart-card-title">SHT45 — Humidity</span>
      <span class="chart-card-sub">Last 60 readings</span>
    </div>
    <div class="chart-wrap"><canvas id="chart-sht-h"></canvas></div>
  </div>
  <div class="chart-card">
    <div class="chart-card-hdr">
      <span class="chart-card-title">PT1000 — Temperature</span>
      <span class="chart-card-sub">Last 60 readings</span>
    </div>
    <div class="chart-wrap"><canvas id="chart-pt"></canvas></div>
  </div>

  <!-- MOSFET + KCS208 -->
  <div class="grid-2">
    <div class="card">
      <div class="card-title">MOSFET Switches — PCF8575</div>
      <div id="mosfet-list"></div>
    </div>
    <div class="card">
      <div class="card-title">Heater Controller — KCS208</div>
      <div class="kcs-grid">
        <div class="kcs-item"><div class="kcs-label">Process Value</div><div class="kcs-value" id="kcs-pv">—</div></div>
        <div class="kcs-item"><div class="kcs-label">Setpoint</div><div class="kcs-value" id="kcs-sv">—</div></div>
        <div class="kcs-item"><div class="kcs-label">Output %</div><div class="kcs-value" id="kcs-mv">—</div></div>
        <div class="kcs-item"><div class="kcs-label">State</div><div style="margin-top:6px"><span id="kcs-badge">—</span></div></div>
      </div>
      <div class="row">
        <div><label>New Setpoint (°C)</label><input type="number" id="kcs-sv-inp" min="0" max="400" style="width:120px"></div>
        <button class="btn-warn"    id="btn-kcs-sv"  style="margin-top:18px">Set SV</button>
        <button class="btn-primary" id="btn-kcs-run" style="margin-top:18px">Run / Stop</button>
      </div>
    </div>
  </div>

  <!-- Logging -->
  <div class="grid-2">
    <div class="card">
      <div class="card-title">PC CSV Logging</div>
      <div class="row" style="align-items:center">
        <div class="log-dot" id="pc-log-dot"></div>
        <span id="pc-log-status" style="font-size:12px;color:var(--muted)">Not logging</span>
        <button class="btn-primary" id="btn-log-start">Start</button>
        <button class="btn-neutral" id="btn-log-stop" disabled>Stop</button>
        <button class="btn-neutral" id="btn-log-dl"   disabled>Download CSV</button>
      </div>
    </div>
    <div class="card">
      <div class="card-title">ESP32 SD Card Logging</div>
      <div class="row" style="align-items:center;margin-bottom:10px">
        <div><label>Filename (no extension)</label>
          <input type="text" id="sd-filename" placeholder="e.g. run1_coldtest" style="min-width:200px">
        </div>
      </div>
      <div class="row" style="align-items:center">
        <div class="log-dot" id="sd-log-dot"></div>
        <span id="sd-log-status" style="font-size:12px;color:var(--muted)">Unknown — connect to query</span>
        <button class="btn-primary" id="btn-sd-start">Start SD Log</button>
        <button class="btn-neutral" id="btn-sd-stop"  disabled>Stop SD Log</button>
      </div>
      <p style="margin-top:10px;font-size:11px;color:var(--muted)">File written to SD card on ESP32 as <em>filename.CSV</em>. Leave blank for auto-numbered LOG_NNNN.CSV.</p>
    </div>
  </div>

  <!-- WiFi -->
  <div class="card">
    <div class="card-title">WiFi Configuration — sent to ESP32 over serial</div>
    <div class="row">
      <div><label>SSID</label><input type="text"     id="wifi-ssid" placeholder="Network name"  style="min-width:200px"></div>
      <div><label>Password</label><input type="password" id="wifi-pass" placeholder="••••••••" style="min-width:200px"></div>
      <button class="btn-primary" id="btn-wifi" style="margin-top:18px">Send to ESP32</button>
    </div>
    <p style="margin-top:10px;font-size:12px;color:var(--muted)">
      Dashboard accessible on LAN at <strong>http://{{ lan_ip }}:8080</strong> regardless of ESP32 WiFi state.
    </p>
  </div>

</main>
<div id="toast"></div>

<script>
const socket = io();
const MAX_PTS = 60;
const N_SHT   = 16;
const N_PT    = 8;

// ── Colour palettes ───────────────────────────────────────────────────────────
const SHT_COLORS = [
  // Board 1 (CH0-7)
  '#60a5fa','#34d399','#a78bfa','#f472b6','#fb923c','#facc15','#4ade80','#22d3ee',
  // Board 2 (CH8-15)
  '#2563eb','#059669','#7c3aed','#db2777','#ea580c','#ca8a04','#16a34a','#0891b2',
];
const PT_COLORS = [
  '#f87171','#fb923c','#fbbf24','#a3e635',   // Board 1
  '#ef4444','#ea580c','#d97706','#84cc16',   // Board 2
];

// ── Chart helpers ─────────────────────────────────────────────────────────────
const CHART_DEFAULTS = {
  responsive: true, maintainAspectRatio: false, animation: false,
  interaction: { mode: 'index', intersect: false },
  scales: {
    x: { ticks: { maxTicksLimit: 8, color: '#64748b', font:{size:10} }, grid: { color: '#2a2d3a' }, border: { color: '#2a2d3a' } },
    y: { ticks: { color: '#64748b', font:{size:10} }, grid: { color: '#2a2d3a' }, border: { color: '#2a2d3a' } }
  },
  plugins: {
    legend: { labels: { color: '#e2e8f0', boxWidth: 10, font:{size:10} } },
    tooltip: {
      backgroundColor: '#1a1d27',
      borderColor: '#2a2d3a',
      borderWidth: 1,
      titleColor: '#94a3b8',
      bodyColor: '#e2e8f0',
      padding: 10,
      callbacks: {
        label: ctx => ctx.parsed.y === null ? null : ` ${ctx.dataset.label}: ${ctx.parsed.y.toFixed(1)}`
      }
    }
  }
};

function makeDataset(label, color, dashed=false) {
  return {
    label, data: [],
    borderColor: color, backgroundColor: color + '18',
    borderWidth: 1.5, pointRadius: 0, tension: 0.3, fill: false,
    borderDash: dashed ? [4,3] : [],
  };
}

function makeChart(id, datasets, yLabel='', yMin=undefined, yMax=undefined) {
  const yExtra = {};
  if (yMin !== undefined) yExtra.min = yMin;
  if (yMax !== undefined) yExtra.max = yMax;
  return new Chart(document.getElementById(id), {
    type: 'line',
    data: { labels: [], datasets },
    options: { ...CHART_DEFAULTS,
      scales: { ...CHART_DEFAULTS.scales,
        y: { ...CHART_DEFAULTS.scales.y, ...yExtra, title: { display: !!yLabel, text: yLabel, color:'#64748b', font:{size:10} } }
      }
    }
  });
}

function pushChart(chart, timeLabel, values) {
  chart.data.labels.push(timeLabel);
  values.forEach((v, i) => { if (chart.data.datasets[i]) chart.data.datasets[i].data.push(v ?? null); });
  if (chart.data.labels.length > MAX_PTS) {
    chart.data.labels.shift();
    chart.data.datasets.forEach(ds => ds.data.shift());
  }
  chart.update('none');
}

// ── Build sensor cards ────────────────────────────────────────────────────────
for (let i = 0; i < N_SHT; i++) {
  const ch   = i < 8 ? i : i - 8;
  const grid = document.getElementById(i < 8 ? 'sht-grid-b1' : 'sht-grid-b2');
  grid.insertAdjacentHTML('beforeend', `
    <div class="sensor-card">
      <div class="sensor-label">SHT45 CH${ch}</div>
      <div class="sensor-value stale" id="sht-t${i}">—</div>
      <div class="sensor-sub"         id="sht-h${i}">Hum: —</div>
    </div>`);
}

for (let i = 0; i < N_PT; i++) {
  const ch   = i < 4 ? i : i - 4;
  const grid = document.getElementById(i < 4 ? 'pt-grid-b1' : 'pt-grid-b2');
  grid.insertAdjacentHTML('beforeend', `
    <div class="sensor-card">
      <div class="sensor-label">PT1000 CH${ch}</div>
      <div class="sensor-value stale" id="pt-t${i}">—</div>
      <div class="sensor-sub">°C</div>
    </div>`);
}

const mosfetList = document.getElementById('mosfet-list');
for (let i = 0; i < 4; i++) {
  mosfetList.insertAdjacentHTML('beforeend', `
    <div class="mosfet-item">
      <span style="font-weight:600">CH${i} — Board ${i < 2 ? 1 : 2}</span>
      <label class="toggle">
        <input type="checkbox" id="mos-${i}" onchange="sendCmd({cmd:'mosfet',ch:${i},on:this.checked})">
        <div class="toggle-track"></div>
      </label>
    </div>`);
}

// ── Init charts ───────────────────────────────────────────────────────────────
const chartShtT = makeChart('chart-sht-t',
  Array.from({length: N_SHT}, (_, i) => makeDataset(`SHT${i} (B${i<8?1:2} CH${i<8?i:i-8})`, SHT_COLORS[i])),
  '°C', 0, 200);

const chartShtH = makeChart('chart-sht-h',
  Array.from({length: N_SHT}, (_, i) => makeDataset(`SHT${i} (B${i<8?1:2} CH${i<8?i:i-8})`, SHT_COLORS[i])),
  '%', 0, 100);

const chartPt = makeChart('chart-pt',
  Array.from({length: N_PT}, (_, i) => makeDataset(`PT${i} (B${i<4?1:2} CH${i<4?i:i-4})`, PT_COLORS[i])),
  '°C', 0, 200);

// No battery history chart — values shown in header

// ── Legend filtering — hide datasets with no valid data ───────────────────────
function filterLegend(chart) {
  let changed = false;
  chart.data.datasets.forEach(ds => {
    const hasData = ds.data.some(v => v !== null && v !== undefined);
    if (ds.hidden !== !hasData) { ds.hidden = !hasData; changed = true; }
  });
  if (changed) chart.update('none');
}

// ── Refresh rate tracking ─────────────────────────────────────────────────────
let _lastRx = null;
const _rateHistory = [];

function updateRefreshRate() {
  const now = Date.now();
  if (_lastRx) {
    _rateHistory.push((now - _lastRx) / 1000);
    if (_rateHistory.length > 6) _rateHistory.shift();
    const avg = _rateHistory.reduce((a,b) => a+b) / _rateHistory.length;
    document.getElementById('refresh-rate').textContent = avg.toFixed(1) + ' s';
  }
  _lastRx = now;
}

// ── Telemetry ─────────────────────────────────────────────────────────────────
socket.on('telemetry', d => {
  updateRefreshRate();
  const ts = new Date().toLocaleTimeString();

  // SHT45 — 0.0 temp AND 0.0 hum means sensor not connected
  const shtTemps = [], shtHums = [];
  for (let i = 0; i < N_SHT; i++) {
    const s = (d.sht || [])[i];
    const notConn = !s || (s.t === 0.0 && s.h === 0.0);
    const t = document.getElementById(`sht-t${i}`);
    const h = document.getElementById(`sht-h${i}`);
    if (t) { t.textContent = notConn ? '—' : s.t.toFixed(1) + ' °C'; t.className = 'sensor-value' + (notConn ? ' stale' : ''); }
    if (h)   h.textContent = notConn ? 'Hum: —' : `Hum: ${s.h.toFixed(1)} %`;
    shtTemps.push(notConn ? null : s.t);
    shtHums.push(notConn  ? null : s.h);
  }
  pushChart(chartShtT, ts, shtTemps);
  pushChart(chartShtH, ts, shtHums);
  filterLegend(chartShtT);
  filterLegend(chartShtH);

  // PT1000 — reading near -242 means open circuit / not connected
  const ptTemps = [];
  for (let i = 0; i < N_PT; i++) {
    const p = (d.pt1000 || [])[i];
    const notConn = !p || p.t < -200 || p.t > 900;
    const t = document.getElementById(`pt-t${i}`);
    if (t) { t.textContent = notConn ? '—' : p.t.toFixed(1); t.className = 'sensor-value' + (notConn ? ' stale' : ''); }
    ptTemps.push(notConn ? null : p.t);
  }
  pushChart(chartPt, ts, ptTemps);
  filterLegend(chartPt);

  // SD card — header indicator
  if (d.sd != null) {
    const dot   = document.getElementById('sd-hdr-dot');
    const label = document.getElementById('sd-hdr-label');
    if (d.sd.logging) {
      dot.className = 'logging';
      label.textContent = 'SD REC';
    } else if (d.sd.present) {
      dot.className = 'present';
      label.textContent = 'SD OK';
    } else {
      dot.className = '';
      label.textContent = 'SD —';
    }
  }

  // Battery — header indicators
  if (d.batt) {
    const soc  = d.batt.soc ?? 0;
    const socColor = soc < 20 ? 'var(--danger)' : soc < 40 ? 'var(--warn)' : 'var(--ok)';
    document.getElementById('batt-hdr-v').textContent    = d.batt.v != null ? d.batt.v.toFixed(3) + ' V' : '— V';
    document.getElementById('batt-hdr-soc').textContent  = d.batt.soc != null ? soc.toFixed(1) + ' %' : '— %';
    const fill = document.getElementById('batt-hdr-fill');
    fill.style.width      = Math.min(soc, 100) + '%';
    fill.style.background = socColor;
    document.getElementById('batt-hdr-v').style.color = socColor === 'var(--ok)' ? 'var(--accent)' : socColor;
  }

  // MOSFET
  (d.mosfet || []).forEach((on, i) => {
    const cb = document.getElementById(`mos-${i}`);
    if (cb) cb.checked = !!on;
  });

  // KCS208 — pv === 0 means controller not connected
  if (d.kcs208) {
    const k = d.kcs208;
    const kConn = k.pv !== 0;
    document.getElementById('kcs-pv').textContent = kConn ? k.pv + ' °C' : '—';
    document.getElementById('kcs-sv').textContent = kConn ? k.sv + ' °C' : '—';
    document.getElementById('kcs-mv').textContent = kConn ? k.mv + ' %'  : '—';
    const badge = document.getElementById('kcs-badge');
    if (!kConn) {
      badge.textContent = 'NOT CONNECTED';
      badge.style.background = 'var(--border)';
      badge.style.color      = 'var(--muted)';
    } else if (k.run != null) {
      badge.textContent = k.run ? 'RUNNING' : 'STOPPED';
      badge.style.background = k.run ? 'var(--ok)' : 'var(--border)';
      badge.style.color      = k.run ? '#000'      : 'var(--muted)';
    }
  }
});

// ── Connection ────────────────────────────────────────────────────────────────
let connected = false;

function loadPorts() {
  fetch('/api/ports')
    .then(r => r.json())
    .then(ports => {
      const sel = document.getElementById('sel-port');
      sel.innerHTML = ports.length
        ? ports.map(p => `<option value="${p.device}">${p.device} — ${p.description}</option>`).join('')
        : '<option value="">No ports found — plug in device then refresh</option>';
    })
    .catch(() => {
      document.getElementById('sel-port').innerHTML = '<option value="">Could not reach server</option>';
    });
}
document.getElementById('btn-ports').onclick = loadPorts;

document.getElementById('btn-connect').onclick = () => {
  if (connected) {
    fetch('/api/disconnect', {method:'POST'}).then(() => setConnected(false));
    return;
  }
  const port = document.getElementById('sel-port').value;
  const baud = document.getElementById('sel-baud').value;
  fetch('/api/connect', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({port, baud})
  }).then(r => r.json()).then(r => {
    if (r.ok) setConnected(true);
    else toast('Connect failed: ' + r.error, true);
  });
};

function setConnected(state) {
  connected = state;
  document.getElementById('conn-dot').className   = state ? 'on' : '';
  document.getElementById('conn-label').textContent = state ? 'Connected' : 'Disconnected';
  const btn = document.getElementById('btn-connect');
  btn.textContent = state ? 'Disconnect' : 'Connect';
  btn.className   = state ? 'btn-danger'  : 'btn-primary';
}

// ── Commands ──────────────────────────────────────────────────────────────────
function sendCmd(obj) {
  fetch('/api/command', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(obj)});
}

document.getElementById('btn-kcs-sv').onclick = () => {
  const val = parseInt(document.getElementById('kcs-sv-inp').value);
  if (isNaN(val)) { toast('Enter a valid setpoint', true); return; }
  sendCmd({cmd:'kcs208_sv', val});
  toast(`Setpoint → ${val} °C`);
};
document.getElementById('btn-kcs-run').onclick = () => {
  const running = document.getElementById('kcs-badge').textContent === 'RUNNING';
  sendCmd({cmd:'kcs208_run', run: !running});
  toast(running ? 'Stop sent' : 'Run sent');
};

// ── PC CSV logging ─────────────────────────────────────────────────────────────
document.getElementById('btn-log-start').onclick = () => {
  fetch('/api/log/start', {method:'POST'}).then(r => r.json()).then(r => {
    if (!r.ok) { toast(r.error, true); return; }
    document.getElementById('pc-log-dot').className    = 'log-dot active';
    document.getElementById('pc-log-status').textContent = 'Logging → ' + r.file;
    document.getElementById('btn-log-start').disabled = true;
    document.getElementById('btn-log-stop').disabled  = false;
    document.getElementById('btn-log-dl').disabled    = true;
  });
};
document.getElementById('btn-log-stop').onclick = () => {
  fetch('/api/log/stop', {method:'POST'}).then(r => r.json()).then(r => {
    document.getElementById('pc-log-dot').className    = 'log-dot';
    document.getElementById('pc-log-status').textContent = 'Saved: ' + r.file;
    document.getElementById('btn-log-start').disabled = false;
    document.getElementById('btn-log-stop').disabled  = true;
    document.getElementById('btn-log-dl').disabled    = false;
  });
};
document.getElementById('btn-log-dl').onclick = () => { window.location = '/api/log/download'; };

// ── ESP32 SD logging ──────────────────────────────────────────────────────────
let sdLogging = false;
document.getElementById('btn-sd-start').onclick = () => {
  const name = document.getElementById('sd-filename').value.trim();
  sendCmd({cmd:'sd_log', active:true, name: name || undefined});
  sdLogging = true;
  document.getElementById('sd-log-dot').className      = 'log-dot active';
  document.getElementById('sd-log-status').textContent = name ? `Logging → ${name}.CSV` : 'Logging → LOG_NNNN.CSV';
  document.getElementById('btn-sd-start').disabled     = true;
  document.getElementById('btn-sd-stop').disabled      = false;
  toast('SD log start sent to ESP32');
};
document.getElementById('btn-sd-stop').onclick = () => {
  sendCmd({cmd:'sd_log', active:false});
  sdLogging = false;
  document.getElementById('sd-log-dot').className    = 'log-dot';
  document.getElementById('sd-log-status').textContent = 'SD logging stopped';
  document.getElementById('btn-sd-start').disabled   = false;
  document.getElementById('btn-sd-stop').disabled    = true;
  toast('SD log stop sent to ESP32');
};

// ── WiFi ──────────────────────────────────────────────────────────────────────
document.getElementById('btn-wifi').onclick = () => {
  const ssid = document.getElementById('wifi-ssid').value.trim();
  const pass = document.getElementById('wifi-pass').value;
  if (!ssid) { toast('Enter an SSID', true); return; }
  sendCmd({cmd:'wifi', ssid, pass});
  toast('WiFi credentials sent to ESP32');
};

// ── Toast ─────────────────────────────────────────────────────────────────────
let _tt;
function toast(msg, err=false) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.className = 'show' + (err ? ' err' : '');
  clearTimeout(_tt);
  _tt = setTimeout(() => el.className = '', 3200);
}

loadPorts();
</script>
</body>
</html>"""


# ──────────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────────

def _lan_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(('8.8.8.8', 80))
            return s.getsockname()[0]
    except Exception:
        return 'localhost'

@app.route('/')
def index():
    return render_template_string(_HTML, lan_ip=_lan_ip())


@app.route('/api/ports')
def api_ports():
    ports = [{'device': p.device, 'description': p.description}
             for p in serial.tools.list_ports.comports()]
    return jsonify(ports)


@app.route('/api/connect', methods=['POST'])
def api_connect():
    global _ser
    data = request.get_json()
    port = data.get('port')
    baud = int(data.get('baud', 115200))
    with _ser_lock:
        if _ser and _ser.is_open:
            _ser.close()
        try:
            _ser = serial.Serial(port, baud, timeout=1)
        except Exception as e:
            return jsonify({'ok': False, 'error': str(e)})
    threading.Thread(target=_serial_reader, daemon=True).start()
    return jsonify({'ok': True})


@app.route('/api/disconnect', methods=['POST'])
def api_disconnect():
    global _ser
    with _ser_lock:
        if _ser and _ser.is_open:
            _ser.close()
    return jsonify({'ok': True})


@app.route('/api/command', methods=['POST'])
def api_command():
    _send_to_esp32(request.get_json())
    return jsonify({'ok': True})


@app.route('/api/log/start', methods=['POST'])
def api_log_start():
    global _log_active, _log_path, _log_fh, _log_writer
    if _log_active:
        return jsonify({'ok': False, 'error': 'Already logging'})
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    _log_path = Path(f'xplora_{ts}.csv')
    _log_fh = open(_log_path, 'w', newline='')
    _log_writer = csv.writer(_log_fh)
    _log_writer.writerow([
        'timestamp',
        *[f'sht{i}_t'  for i in range(N_SHT)],
        *[f'sht{i}_h'  for i in range(N_SHT)],
        *[f'pt{i}_t'   for i in range(N_PT)],
        'batt_v', 'batt_soc',
        *[f'mosfet{i}' for i in range(4)],
        'kcs_pv', 'kcs_sv', 'kcs_mv', 'kcs_run',
    ])
    _log_active = True
    return jsonify({'ok': True, 'file': str(_log_path)})


@app.route('/api/log/stop', methods=['POST'])
def api_log_stop():
    global _log_active, _log_fh
    _log_active = False
    if _log_fh:
        _log_fh.close()
        _log_fh = None
    return jsonify({'ok': True, 'file': str(_log_path)})


@app.route('/api/log/download')
def api_log_download():
    if _log_path and _log_path.exists():
        return send_file(_log_path.resolve(), as_attachment=True)
    return jsonify({'error': 'No log file available'}), 404


# ──────────────────────────────────────────────────────────────────────────────
# Serial
# ──────────────────────────────────────────────────────────────────────────────

def _send_to_esp32(obj):
    with _ser_lock:
        if _ser and _ser.is_open:
            _ser.write((json.dumps(obj) + '\n').encode())


def _serial_reader():
    global _latest
    while True:
        with _ser_lock:
            alive = bool(_ser and _ser.is_open)
            ser_ref = _ser
        if not alive:
            break
        try:
            raw = ser_ref.readline()
        except Exception:
            break
        line = raw.decode('utf-8', errors='ignore').strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        _latest = data
        socketio.emit('telemetry', data)
        _log_row(data)


def _log_row(data):
    if not _log_active or not _log_writer:
        return
    sht    = data.get('sht',    [{}] * N_SHT)
    pt     = data.get('pt1000', [{}] * N_PT)
    batt   = data.get('batt',   {})
    mosfet = data.get('mosfet', [False] * 4)
    kcs    = data.get('kcs208', {})
    _log_writer.writerow([
        datetime.now().isoformat(),
        *[sht[i].get('t', '') if i < len(sht) else '' for i in range(N_SHT)],
        *[sht[i].get('h', '') if i < len(sht) else '' for i in range(N_SHT)],
        *[(lambda v: '' if v is None or v < -200 or v > 900 else v)(pt[i].get('t') if i < len(pt) else None) for i in range(N_PT)],
        batt.get('v', ''), batt.get('soc', ''),
        *[mosfet[i] if i < len(mosfet) else '' for i in range(4)],
        kcs.get('pv', ''), kcs.get('sv', ''), kcs.get('mv', ''), kcs.get('run', ''),
    ])
    _log_fh.flush()


# ──────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    HOST, PORT = '0.0.0.0', 8080
    lan = _lan_ip()
    print('=' * 48)
    print('  XploraVentures Dashboard')
    print(f'  http://localhost:{PORT}')
    print(f'  http://{lan}:{PORT}  ← share on LAN')
    print('=' * 48)
    webbrowser.open(f'http://localhost:{PORT}')
    socketio.run(app, host=HOST, port=PORT, use_reloader=False)
