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

import contextlib
import csv
import io
import json
import os
import socket
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

import serial
import serial.tools.list_ports
from flask import Flask, jsonify, render_template_string, request, send_file
from flask_socketio import SocketIO

VERSION      = "1.1.0"
GITHUB_REPO  = "brorook/XploraVentures"

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
_update_info  = None  # set by update-check thread if a newer release exists
_latest_release = None  # full latest release info (assets, tag, etc.)

N_SHT = 16
N_PT  = 8

# ──────────────────────────────────────────────────────────────────────────────
_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>XploraVentures</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<script src="https://cdn.socket.io/4.7.5/socket.io.min.js"></script>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
:root {
  --bg:      #0d0f14;
  --surface: #13161e;
  --surface2:#181b25;
  --border:  #222535;
  --accent:  #00c9a7;
  --text:    #dde3ed;
  --muted:   #515a72;
  --warn:    #f59e0b;
  --danger:  #ef4444;
  --ok:      #10b981;
  --r:       8px;
}
body { background: var(--bg); color: var(--text); font-family: 'Inter', system-ui, sans-serif; font-size: 14px; line-height: 1.5; -webkit-font-smoothing: antialiased; }

/* ── Header ── */
header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 28px; height: 52px;
  background: var(--surface); border-bottom: 1px solid var(--border);
  position: sticky; top: 0; z-index: 10;
}
.hdr-logo    { font-size: 15px; font-weight: 700; color: var(--accent); letter-spacing: -.2px; }
.hdr-version { font-size: 11px; color: var(--muted); font-weight: 500; }
.hdr-right { display: flex; align-items: center; gap: 18px; }

.hdr-pill { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); background: var(--border); padding: 4px 11px; border-radius: 20px; }
.hdr-pill-val { color: var(--accent); font-weight: 600; margin-left: 2px; }
.hdr-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); flex-shrink: 0; transition: background .3s; }
.hdr-dot.ok  { background: var(--ok); }
.hdr-dot.rec { background: var(--danger); animation: blink 1s infinite; }

.batt-wrap { display: flex; align-items: center; gap: 8px; }
.batt-v    { font-size: 13px; font-weight: 600; color: var(--accent); min-width: 54px; font-variant-numeric: tabular-nums; }
.batt-bar  { width: 44px; height: 4px; background: var(--border); border-radius: 4px; overflow: hidden; }
.batt-fill { height: 100%; background: var(--ok); border-radius: 4px; width: 0%; transition: width .6s, background .3s; }
.batt-soc  { font-size: 12px; color: var(--muted); min-width: 36px; font-variant-numeric: tabular-nums; }

.conn-wrap { display: flex; align-items: center; gap: 7px; font-size: 13px; color: var(--muted); }
.conn-dot  { width: 8px; height: 8px; border-radius: 50%; background: var(--muted); transition: background .3s; }
.conn-dot.on { background: var(--ok); box-shadow: 0 0 5px var(--ok); }

/* ── Layout ── */
main { padding: 24px 28px; display: flex; flex-direction: column; gap: 14px; max-width: 1600px; margin: 0 auto; }

/* ── Cards ── */
.card { background: var(--surface); border: 1px solid var(--border); border-radius: var(--r); padding: 20px 22px; }
.card-hdr { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 18px; }
.card-title { font-size: 13px; font-weight: 600; color: var(--text); }
.card-sub   { font-size: 11px; color: var(--muted); }

/* ── Forms ── */
.field { display: flex; flex-direction: column; }
.field label { font-size: 11px; font-weight: 500; color: var(--muted); text-transform: uppercase; letter-spacing: .6px; margin-bottom: 6px; }
select, input[type=text], input[type=password], input[type=number] {
  background: var(--bg); border: 1px solid var(--border); color: var(--text);
  border-radius: 6px; padding: 8px 11px; font-size: 13px; font-family: inherit; outline: none;
}
select:focus, input:focus { border-color: var(--accent); }
.field-sm select, .field-sm input { min-width: 110px; }
.field-md select, .field-md input { min-width: 190px; }
.field-lg select, .field-lg input { min-width: 240px; }

.row { display: flex; flex-wrap: wrap; gap: 12px; align-items: flex-end; }

button { padding: 8px 16px; border-radius: 6px; border: none; cursor: pointer; font-size: 13px; font-weight: 600; font-family: inherit; transition: opacity .15s, transform .08s; white-space: nowrap; }
button:hover:not(:disabled) { opacity: .84; }
button:active:not(:disabled) { transform: scale(.97); }
button:disabled { opacity: .28; cursor: default; }
.btn-primary { background: var(--accent); color: #000; }
.btn-danger  { background: var(--danger); color: #fff; }
.btn-warn    { background: var(--warn);   color: #000; }
.btn-ghost   { background: var(--border); color: var(--text); }

/* ── Sensor grids ── */
.sensor-group { display: flex; flex-direction: column; gap: 14px; }
.board-label  { font-size: 10px; font-weight: 600; text-transform: uppercase; letter-spacing: 1.2px; color: var(--muted); border-bottom: 1px solid var(--border); padding-bottom: 7px; margin-bottom: 10px; }
.sht-grid { display: grid; grid-template-columns: repeat(8, 1fr); gap: 8px; }
.pt-grid  { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; }

.sensor-card { background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 12px 13px; }
.sensor-name { font-size: 10px; font-weight: 500; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; margin-bottom: 7px; }
.sensor-val  { font-size: 19px; font-weight: 700; color: var(--accent); line-height: 1; font-variant-numeric: tabular-nums; }
.sensor-val.stale { color: var(--muted); }
.sensor-aux  { font-size: 11px; color: var(--muted); margin-top: 4px; }

/* ── Two-column ── */
.grid-2 { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 14px; }
.grid-3c { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }

/* ── MOSFET ── */
.mosfet-item { display: flex; align-items: center; justify-content: space-between; padding: 11px 14px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; }
.mosfet-item + .mosfet-item { margin-top: 7px; }
.mosfet-ch   { font-size: 13px; font-weight: 600; }
.mosfet-board{ font-size: 11px; color: var(--muted); margin-top: 1px; }

.toggle { position: relative; width: 40px; height: 22px; flex-shrink: 0; }
.toggle input { opacity: 0; width: 0; height: 0; }
.toggle-track { position: absolute; inset: 0; background: var(--border); border-radius: 22px; cursor: pointer; transition: background .2s; }
.toggle input:checked + .toggle-track { background: var(--accent); }
.toggle-track::after { content: ''; position: absolute; left: 3px; top: 3px; width: 16px; height: 16px; border-radius: 50%; background: #fff; transition: transform .2s; }
.toggle input:checked + .toggle-track::after { transform: translateX(18px); }

/* ── KCS208 ── */
.kcs-stats { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-bottom: 16px; }
.kcs-stat  { background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 12px 13px; }
.kcs-stat-label { font-size: 10px; font-weight: 500; color: var(--muted); text-transform: uppercase; letter-spacing: .5px; }
.kcs-stat-val   { font-size: 19px; font-weight: 700; margin-top: 5px; font-variant-numeric: tabular-nums; }
.kcs-badge { display: inline-flex; align-items: center; padding: 3px 10px; border-radius: 20px; font-size: 11px; font-weight: 600; background: var(--border); color: var(--muted); }

/* ── Logging ── */
.log-row   { display: flex; align-items: center; gap: 9px; }
.log-dot   { width: 7px; height: 7px; border-radius: 50%; background: var(--muted); flex-shrink: 0; }
.log-dot.active { background: var(--danger); animation: blink 1s infinite; }
.log-status{ font-size: 12px; color: var(--muted); flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.section-label { font-size: 11px; font-weight: 600; color: var(--muted); text-transform: uppercase; letter-spacing: .7px; margin-bottom: 10px; }
.divider   { height: 1px; background: var(--border); margin: 18px 0; }
.note      { font-size: 11px; color: var(--muted); margin-top: 10px; line-height: 1.6; }

@keyframes blink { 0%,100%{opacity:1} 50%{opacity:.25} }

/* ── Firmware flash ── */
.flash-zone { border: 1.5px dashed var(--border); border-radius: 6px; padding: 18px; text-align: center; cursor: pointer; transition: border-color .2s, background .2s; margin-bottom: 14px; }
.flash-zone:hover, .flash-zone.drag { border-color: var(--accent); background: rgba(0,201,167,.04); }
.flash-zone input[type=file] { display: none; }
.flash-zone-label { font-size: 13px; color: var(--muted); }
.flash-zone-label strong { color: var(--accent); }
.flash-file-name { font-size: 12px; color: var(--text); margin-top: 6px; font-weight: 500; }
.flash-log { background: var(--bg); border: 1px solid var(--border); border-radius: 6px; padding: 10px 12px; font-family: 'Menlo', 'Consolas', monospace; font-size: 11px; color: var(--muted); height: 140px; overflow-y: auto; white-space: pre-wrap; word-break: break-all; margin-top: 14px; display: none; }
.flash-log.show { display: block; }
.flash-log .ok  { color: var(--ok); }
.flash-log .err { color: var(--danger); }
.flash-warn { font-size: 11px; color: var(--warn); margin-top: 10px; }

/* ── Update banner ── */
#update-banner { display: none; align-items: center; justify-content: center; gap: 12px; padding: 9px 24px; background: #1a2a1e; border-bottom: 1px solid #2d5a38; font-size: 13px; }
#update-banner.show { display: flex; }
#update-banner a { color: var(--accent); font-weight: 600; text-decoration: none; }
#update-banner a:hover { text-decoration: underline; }
.update-dismiss { background: none; border: none; color: var(--muted); cursor: pointer; font-size: 16px; padding: 0 4px; line-height: 1; }

/* ── Toast ── */
#toast { position: fixed; bottom: 24px; right: 24px; background: var(--surface2); border: 1px solid var(--accent); padding: 11px 16px; border-radius: var(--r); font-size: 13px; opacity: 0; transition: opacity .25s; pointer-events: none; max-width: 300px; box-shadow: 0 8px 28px rgba(0,0,0,.5); }
#toast.err  { border-color: var(--danger); }
#toast.show { opacity: 1; }
</style>
</head>
<body>

<header>
  <div style="display:flex;align-items:baseline;gap:8px">
    <span class="hdr-logo">XploraVentures</span>
    <span class="hdr-version">Dashboard v{{ version }}</span>
    <span class="hdr-version" id="fw-version" style="display:none">· Firmware <span id="fw-version-val">—</span></span>
  </div>
  <div class="hdr-right">
    <div class="hdr-pill">
      <div class="hdr-dot" id="sd-hdr-dot"></div>
      <span id="sd-hdr-label">SD —</span>
    </div>
    <div class="batt-wrap">
      <span class="batt-v"   id="batt-hdr-v">— V</span>
      <div  class="batt-bar"><div class="batt-fill" id="batt-hdr-fill"></div></div>
      <span class="batt-soc" id="batt-hdr-soc">— %</span>
    </div>
    <div class="hdr-pill">Refresh<span class="hdr-pill-val" id="refresh-rate">—</span></div>
    <div class="conn-wrap">
      <div class="conn-dot" id="conn-dot"></div>
      <span id="conn-label">Disconnected</span>
    </div>
  </div>
</header>

<div id="update-banner">
  <span id="update-text"></span>
  <button class="update-dismiss" onclick="document.getElementById('update-banner').classList.remove('show')" title="Dismiss">✕</button>
</div>

<main>

  <!-- Connection -->
  <div class="card">
    <div class="card-hdr"><span class="card-title">Serial Connection</span></div>
    <div class="row">
      <div class="field field-lg"><label>Port</label><select id="sel-port"><option>Loading…</option></select></div>
      <div class="field field-sm"><label>Baud Rate</label>
        <select id="sel-baud">
          <option value="115200" selected>115200</option>
          <option value="921600">921600</option>
          <option value="9600">9600</option>
        </select>
      </div>
      <button class="btn-primary" id="btn-connect">Connect</button>
      <button class="btn-ghost"   id="btn-ports">Refresh Ports</button>
    </div>
  </div>

  <!-- Firmware update -->
  <div class="card">
    <div class="card-hdr">
      <span class="card-title">Firmware Update</span>
      <span class="card-sub">ESP32 — via serial (USB)</span>
    </div>

    <!-- GitHub release flash -->
    <div id="github-flash-row" style="display:none;margin-bottom:16px">
      <div class="section-label" style="margin-bottom:8px">From GitHub Release</div>
      <div class="row" style="align-items:center">
        <span id="github-fw-label" style="font-size:13px;color:var(--muted);flex:1"></span>
        <button class="btn-primary" id="btn-flash-github">Flash from Release</button>
      </div>
    </div>

    <!-- Manual upload flash -->
    <div class="section-label" style="margin-bottom:8px">Manual Upload</div>
    <div class="row" style="margin-bottom:14px;align-items:flex-end">
      <div class="field" style="flex:1">
        <div class="flash-zone" id="flash-zone" onclick="document.getElementById('flash-file').click()">
          <input type="file" id="flash-file" accept=".bin">
          <div class="flash-zone-label">Drop <strong>.bin</strong> here or click to browse</div>
          <div class="flash-file-name" id="flash-file-name"></div>
        </div>
      </div>
      <div class="field field-sm"><label>Flash Address</label><input type="text" id="flash-addr" value="0x10000" placeholder="0x10000"></div>
      <button class="btn-danger" id="btn-flash" disabled>Flash Firmware</button>
    </div>

    <div class="flash-log" id="flash-log"></div>
    <p class="flash-warn">⚠ Flashing will briefly disconnect the ESP32. Do not close the dashboard during the process.</p>
  </div>

  <!-- Logging + Heater Controller + MOSFET — all one row -->
  <div class="grid-3c">

    <div class="card">
      <div class="card-hdr"><span class="card-title">Logging</span></div>

      <div class="section-label">PC CSV</div>
      <div class="log-row" style="margin-bottom:10px">
        <div class="log-dot" id="pc-log-dot"></div>
        <span class="log-status" id="pc-log-status">Not logging</span>
      </div>
      <div class="row">
        <button class="btn-primary" id="btn-log-start">Start</button>
        <button class="btn-ghost"   id="btn-log-stop" disabled>Stop</button>
        <button class="btn-ghost"   id="btn-log-dl"   disabled>Download CSV</button>
      </div>

      <div class="divider"></div>

      <div class="section-label">ESP32 SD Card</div>
      <div class="field" style="margin-bottom:12px">
        <label>Filename (no extension)</label>
        <input type="text" id="sd-filename" placeholder="e.g. run1_coldtest">
      </div>
      <div class="log-row" style="margin-bottom:10px">
        <div class="log-dot" id="sd-log-dot"></div>
        <span class="log-status" id="sd-log-status">Unknown — connect to query</span>
      </div>
      <div class="row">
        <button class="btn-primary" id="btn-sd-start">Start SD Log</button>
        <button class="btn-ghost"   id="btn-sd-stop"  disabled>Stop SD Log</button>
      </div>
      <p class="note">Leave blank for auto-numbered <em>LOG_NNNN.CSV</em>.</p>
    </div>

    <div class="card">
      <div class="card-hdr">
        <span class="card-title">Heater Controller</span>
        <span class="card-sub">KCS208</span>
      </div>
      <div class="kcs-stats">
        <div class="kcs-stat"><div class="kcs-stat-label">Process Value</div><div class="kcs-stat-val" id="kcs-pv">—</div></div>
        <div class="kcs-stat"><div class="kcs-stat-label">Setpoint</div><div class="kcs-stat-val" id="kcs-sv">—</div></div>
        <div class="kcs-stat"><div class="kcs-stat-label">Output</div><div class="kcs-stat-val" id="kcs-mv">—</div></div>
        <div class="kcs-stat"><div class="kcs-stat-label">State</div><div style="margin-top:6px"><span class="kcs-badge" id="kcs-badge">—</span></div></div>
      </div>
      <div class="row">
        <div class="field field-sm"><label>Setpoint (°C)</label><input type="number" id="kcs-sv-inp" min="0" max="400"></div>
        <button class="btn-warn"    id="btn-kcs-sv">Set SV</button>
        <button class="btn-primary" id="btn-kcs-run">Run / Stop</button>
      </div>
    </div>

    <div class="card">
      <div class="card-hdr">
        <span class="card-title">MOSFET Switches</span>
        <span class="card-sub">PCF8575</span>
      </div>
      <div id="mosfet-list"></div>
    </div>

  </div>

  <!-- SHT45 -->
  <div class="card">
    <div class="card-hdr">
      <span class="card-title">Temperature &amp; Humidity</span>
      <span class="card-sub">SHT45 — up to ×16</span>
    </div>
    <div class="sensor-group">
      <div><div class="board-label">Board 1</div><div class="sht-grid" id="sht-grid-b1"></div></div>
      <div><div class="board-label">Board 2</div><div class="sht-grid" id="sht-grid-b2"></div></div>
    </div>
  </div>

  <!-- PT1000 -->
  <div class="card">
    <div class="card-hdr">
      <span class="card-title">High-Temperature RTD</span>
      <span class="card-sub">PT1000 — up to ×8</span>
    </div>
    <div class="sensor-group">
      <div><div class="board-label">Board 1</div><div class="pt-grid" id="pt-grid-b1"></div></div>
      <div><div class="board-label">Board 2</div><div class="pt-grid" id="pt-grid-b2"></div></div>
    </div>
  </div>


</main>
<div id="toast"></div>

<script>
const socket = io();
const N_SHT = 16;
const N_PT  = 8;

// ── Build sensor cards ────────────────────────────────────────────────────────
for (let i = 0; i < N_SHT; i++) {
  const ch   = i < 8 ? i : i - 8;
  const grid = document.getElementById(i < 8 ? 'sht-grid-b1' : 'sht-grid-b2');
  grid.insertAdjacentHTML('beforeend', `
    <div class="sensor-card">
      <div class="sensor-name">CH${ch}</div>
      <div class="sensor-val stale" id="sht-t${i}">—</div>
      <div class="sensor-aux"       id="sht-h${i}">Hum: —</div>
    </div>`);
}

for (let i = 0; i < N_PT; i++) {
  const ch   = i < 4 ? i : i - 4;
  const grid = document.getElementById(i < 4 ? 'pt-grid-b1' : 'pt-grid-b2');
  grid.insertAdjacentHTML('beforeend', `
    <div class="sensor-card">
      <div class="sensor-name">CH${ch}</div>
      <div class="sensor-val stale" id="pt-t${i}">—</div>
      <div class="sensor-aux">°C</div>
    </div>`);
}

const mosfetList = document.getElementById('mosfet-list');
for (let i = 0; i < 8; i++) {
  mosfetList.insertAdjacentHTML('beforeend', `
    <div class="mosfet-item">
      <div>
        <div class="mosfet-ch">CH${i}</div>
        <div class="mosfet-board">Board ${i < 4 ? 1 : 2}</div>
      </div>
      <label class="toggle">
        <input type="checkbox" id="mos-${i}" onchange="sendCmd({cmd:'mosfet',ch:${i},on:this.checked})">
        <div class="toggle-track"></div>
      </label>
    </div>`);
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

  // Firmware version
  if (d.fw) {
    document.getElementById('fw-version-val').textContent = d.fw;
    document.getElementById('fw-version').style.display = '';
  }

  // SHT45 — 0.0 temp AND 0.0 hum means sensor not connected
  for (let i = 0; i < N_SHT; i++) {
    const s = (d.sht || [])[i];
    const notConn = !s || (s.t === 0.0 && s.h === 0.0);
    const t = document.getElementById(`sht-t${i}`);
    const h = document.getElementById(`sht-h${i}`);
    if (t) { t.textContent = notConn ? '—' : s.t.toFixed(1) + ' °C'; t.className = 'sensor-val' + (notConn ? ' stale' : ''); }
    if (h)   h.textContent = notConn ? 'Hum: —' : `Hum: ${s.h.toFixed(1)} %`;
  }

  // PT1000 — reading near -242 means open circuit / not connected
  for (let i = 0; i < N_PT; i++) {
    const p = (d.pt1000 || [])[i];
    const notConn = !p || p.t < -200 || p.t > 900;
    const t = document.getElementById(`pt-t${i}`);
    if (t) { t.textContent = notConn ? '—' : p.t.toFixed(1); t.className = 'sensor-val' + (notConn ? ' stale' : ''); }
  }

  // SD card — header indicator
  if (d.sd != null) {
    const dot   = document.getElementById('sd-hdr-dot');
    const label = document.getElementById('sd-hdr-label');
    if (d.sd.logging) {
      dot.className = 'hdr-dot rec'; label.textContent = 'SD REC';
    } else if (d.sd.present) {
      dot.className = 'hdr-dot ok';  label.textContent = 'SD OK';
    } else {
      dot.className = 'hdr-dot';     label.textContent = 'SD —';
    }
  }

  // Battery — header
  if (d.batt) {
    const soc      = d.batt.soc ?? 0;
    const socColor = soc < 20 ? 'var(--danger)' : soc < 40 ? 'var(--warn)' : 'var(--ok)';
    document.getElementById('batt-hdr-v').textContent   = d.batt.v   != null ? d.batt.v.toFixed(3)  + ' V' : '— V';
    document.getElementById('batt-hdr-soc').textContent = d.batt.soc != null ? soc.toFixed(1) + ' %' : '— %';
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
      badge.textContent = 'NOT CONNECTED'; badge.style.background = 'var(--border)'; badge.style.color = 'var(--muted)';
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
    .catch(() => { document.getElementById('sel-port').innerHTML = '<option value="">Could not reach server</option>'; });
}
document.getElementById('btn-ports').onclick = loadPorts;

document.getElementById('btn-connect').onclick = () => {
  if (connected) { fetch('/api/disconnect', {method:'POST'}).then(() => setConnected(false)); return; }
  const port = document.getElementById('sel-port').value;
  const baud = document.getElementById('sel-baud').value;
  fetch('/api/connect', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({port, baud})})
    .then(r => r.json()).then(r => { if (r.ok) setConnected(true); else toast('Connect failed: ' + r.error, true); });
};

function setConnected(state) {
  connected = state;
  document.getElementById('conn-dot').className     = state ? 'conn-dot on' : 'conn-dot';
  document.getElementById('conn-label').textContent = state ? 'Connected' : 'Disconnected';
  const btn = document.getElementById('btn-connect');
  btn.textContent = state ? 'Disconnect' : 'Connect';
  btn.className   = state ? 'btn-danger'  : 'btn-primary';
  if (!state) document.getElementById('fw-version').style.display = 'none';
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

// ── PC CSV logging ────────────────────────────────────────────────────────────
document.getElementById('btn-log-start').onclick = () => {
  fetch('/api/log/start', {method:'POST'}).then(r => r.json()).then(r => {
    if (!r.ok) { toast(r.error, true); return; }
    document.getElementById('pc-log-dot').className      = 'log-dot active';
    document.getElementById('pc-log-status').textContent = 'Logging → ' + r.file;
    document.getElementById('btn-log-start').disabled = true;
    document.getElementById('btn-log-stop').disabled  = false;
    document.getElementById('btn-log-dl').disabled    = true;
  });
};
document.getElementById('btn-log-stop').onclick = () => {
  fetch('/api/log/stop', {method:'POST'}).then(r => r.json()).then(r => {
    document.getElementById('pc-log-dot').className      = 'log-dot';
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
  document.getElementById('btn-sd-start').disabled = true;
  document.getElementById('btn-sd-stop').disabled  = false;
  toast('SD log start sent to ESP32');
};
document.getElementById('btn-sd-stop').onclick = () => {
  sendCmd({cmd:'sd_log', active:false});
  sdLogging = false;
  document.getElementById('sd-log-dot').className      = 'log-dot';
  document.getElementById('sd-log-status').textContent = 'SD logging stopped';
  document.getElementById('btn-sd-start').disabled = false;
  document.getElementById('btn-sd-stop').disabled  = true;
  toast('SD log stop sent to ESP32');
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

// ── Firmware flash ────────────────────────────────────────────────────────────
let _flashFile = null;

const flashZone = document.getElementById('flash-zone');
const flashFileInput = document.getElementById('flash-file');
const flashFileName = document.getElementById('flash-file-name');
const flashBtn = document.getElementById('btn-flash');
const flashLog = document.getElementById('flash-log');

function setFlashFile(file) {
  if (!file || !file.name.endsWith('.bin')) { toast('Select a .bin file', true); return; }
  _flashFile = file;
  flashFileName.textContent = file.name;
  flashBtn.disabled = false;
}

flashFileInput.onchange = () => setFlashFile(flashFileInput.files[0]);

flashZone.addEventListener('dragover',  e => { e.preventDefault(); flashZone.classList.add('drag'); });
flashZone.addEventListener('dragleave', ()  => flashZone.classList.remove('drag'));
flashZone.addEventListener('drop', e => {
  e.preventDefault();
  flashZone.classList.remove('drag');
  setFlashFile(e.dataTransfer.files[0]);
});

function flashAppendLine(text, cls='') {
  flashLog.classList.add('show');
  const span = document.createElement('span');
  if (cls) span.className = cls;
  span.textContent = text + '\n';
  flashLog.appendChild(span);
  flashLog.scrollTop = flashLog.scrollHeight;
}

flashBtn.onclick = () => {
  if (!_flashFile) return;
  const port = document.getElementById('sel-port').value;
  if (!port) { toast('Select a port first', true); return; }

  flashLog.innerHTML = '';
  flashLog.classList.add('show');
  flashBtn.disabled = true;

  const fd = new FormData();
  fd.append('file', _flashFile);
  fd.append('port', port);
  fd.append('addr', document.getElementById('flash-addr').value || '0x10000');

  fetch('/api/firmware/upload', {method:'POST', body: fd})
    .then(r => r.json())
    .then(r => { if (!r.ok) { flashAppendLine('Error: ' + r.error, 'err'); flashBtn.disabled = false; } });
};

socket.on('flash_log',  d => flashAppendLine(d.line));
socket.on('flash_done', d => {
  flashAppendLine(d.msg, d.ok ? 'ok' : 'err');
  flashBtn.disabled = false;
  document.getElementById('btn-flash-github').disabled = false;
  if (d.ok) { _flashFile = null; flashFileName.textContent = ''; flashFileInput.value = ''; }
});

// ── GitHub firmware flash ─────────────────────────────────────────────────────
function applyReleaseInfo(d) {
  if (!d || !d.fw_url) return;
  const row   = document.getElementById('github-flash-row');
  const label = document.getElementById('github-fw-label');
  const size  = d.fw_size ? ` (${(d.fw_size / 1024).toFixed(0)} KB)` : '';
  label.textContent = `firmware.bin — v${d.version}${size}`;
  row.style.display = '';
}

socket.on('release_info', applyReleaseInfo);

document.getElementById('btn-flash-github').onclick = () => {
  const port = document.getElementById('sel-port').value;
  if (!port) { toast('Select a port first', true); return; }
  document.getElementById('flash-log').innerHTML = '';
  document.getElementById('btn-flash-github').disabled = true;
  fetch('/api/firmware/flash-github', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({port})
  }).then(r => r.json()).then(r => {
    if (!r.ok) { flashAppendLine('Error: ' + r.error, 'err'); document.getElementById('btn-flash-github').disabled = false; }
  });
};

// ── Update notification ───────────────────────────────────────────────────────
socket.on('update_available', d => {
  const banner = document.getElementById('update-banner');
  document.getElementById('update-text').innerHTML =
    `Update available — v${d.version} &nbsp;·&nbsp; <a href="${d.url}" target="_blank">Download</a>`;
  banner.classList.add('show');
});

// Check for an update that arrived before the socket connected (e.g. page refresh)
fetch('/api/version').then(r => r.json()).then(d => {
  if (d.update)   socket.emit('update_available', d.update);
  if (d.release)  applyReleaseInfo(d.release);
});

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
    return render_template_string(_HTML, lan_ip=_lan_ip(), version=VERSION)


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
        *[f'mosfet{i}' for i in range(8)],
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
        *[mosfet[i] if i < len(mosfet) else '' for i in range(8)],
        kcs.get('pv', ''), kcs.get('sv', ''), kcs.get('mv', ''), kcs.get('run', ''),
    ])
    _log_fh.flush()


# ──────────────────────────────────────────────────────────────────────────────
# Firmware flash
# ──────────────────────────────────────────────────────────────────────────────

class _FlashWriter:
    """Captures esptool stdout/stderr and emits each line via socketio."""
    def __init__(self):
        self._buf = ''

    def write(self, text):
        self._buf += text
        while '\n' in self._buf:
            line, self._buf = self._buf.split('\n', 1)
            line = line.strip('\r')
            if line:
                socketio.emit('flash_log', {'line': line})

    def flush(self):
        if self._buf.strip():
            socketio.emit('flash_log', {'line': self._buf.strip()})
            self._buf = ''


def _flash_firmware(port, bin_path, addr):
    global _ser
    try:
        socketio.emit('flash_log', {'line': '— Closing serial connection…'})
        with _ser_lock:
            if _ser and _ser.is_open:
                _ser.close()

        socketio.emit('flash_log', {'line': f'— Flashing {os.path.basename(bin_path)} → {addr}'})

        import esptool
        writer = _FlashWriter()
        with contextlib.redirect_stdout(writer), contextlib.redirect_stderr(writer):
            try:
                esptool.main([
                    '--port', port,
                    '--baud', '460800',
                    '--chip', 'esp32',
                    'write_flash', '-z', addr, bin_path,
                ])
            except SystemExit as e:
                if e.code not in (None, 0):
                    raise RuntimeError(f'esptool exited with code {e.code}')

        socketio.emit('flash_done', {'ok': True,  'msg': 'Flash complete — ESP32 rebooting…'})

    except Exception as e:
        socketio.emit('flash_done', {'ok': False, 'msg': f'Flash failed: {e}'})
    finally:
        try:
            os.unlink(bin_path)
        except OSError:
            pass


@app.route('/api/firmware/upload', methods=['POST'])
def api_firmware_upload():
    f    = request.files.get('file')
    port = request.form.get('port', '').strip()
    addr = request.form.get('addr', '0x10000').strip() or '0x10000'

    if not f or not f.filename.endswith('.bin'):
        return jsonify({'ok': False, 'error': 'A .bin file is required'})
    if not port:
        return jsonify({'ok': False, 'error': 'No port selected — connect to ESP32 first'})

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.bin')
    f.save(tmp.name)
    tmp.close()

    threading.Thread(target=_flash_firmware, args=(port, tmp.name, addr), daemon=True).start()
    return jsonify({'ok': True})


# ──────────────────────────────────────────────────────────────────────────────
# Update check
# ──────────────────────────────────────────────────────────────────────────────

def _parse_version(tag: str):
    """Convert 'v1.2.3' or '1.2.3' to a comparable tuple."""
    return tuple(int(x) for x in tag.lstrip('v').split('.'))


def _check_for_update():
    """Background thread: polls GitHub releases once, notifies clients of updates."""
    global _update_info, _latest_release
    time.sleep(5)
    url = f'https://api.github.com/repos/{GITHUB_REPO}/releases/latest'
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'XploraVentures'})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        latest_tag = data.get('tag_name', '')
        if not latest_tag:
            return
        assets = data.get('assets', [])
        exe_url = next((a['browser_download_url'] for a in assets if a['name'].endswith('.exe')), data.get('html_url', ''))
        fw_url  = next((a['browser_download_url'] for a in assets if a['name'] == 'firmware.bin'), None)
        fw_size = next((a['size'] for a in assets if a['name'] == 'firmware.bin'), None)

        _latest_release = {
            'version': latest_tag.lstrip('v'),
            'url':     exe_url,
            'fw_url':  fw_url,
            'fw_size': fw_size,
        }
        socketio.emit('release_info', _latest_release)

        if _parse_version(latest_tag) > _parse_version(VERSION):
            _update_info = _latest_release
            socketio.emit('update_available', _update_info)
            print(f'  [update] New version {latest_tag} available')
        else:
            print(f'  [update] Up to date ({latest_tag}), firmware.bin {"found" if fw_url else "not in release"}')
    except Exception as e:
        print(f'  [update] Check failed: {e}')


@app.route('/api/version')
def api_version():
    return jsonify({'version': VERSION, 'update': _update_info, 'release': _latest_release})


@app.route('/api/firmware/flash-github', methods=['POST'])
def api_firmware_flash_github():
    port = request.get_json().get('port', '').strip()
    if not port:
        return jsonify({'ok': False, 'error': 'No port selected'})
    if not _latest_release or not _latest_release.get('fw_url'):
        return jsonify({'ok': False, 'error': 'No firmware.bin in latest release'})

    def _download_and_flash():
        try:
            socketio.emit('flash_log', {'line': f'— Downloading firmware v{_latest_release["version"]} from GitHub…'})
            req = urllib.request.Request(_latest_release['fw_url'], headers={'User-Agent': 'XploraVentures'})
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.bin')
            with urllib.request.urlopen(req, timeout=60) as resp:
                tmp.write(resp.read())
            tmp.close()
            socketio.emit('flash_log', {'line': f'— Download complete ({os.path.getsize(tmp.name) // 1024} KB)'})
            _flash_firmware(port, tmp.name, '0x10000')
        except Exception as e:
            socketio.emit('flash_done', {'ok': False, 'msg': f'Download failed: {e}'})

    threading.Thread(target=_download_and_flash, daemon=True).start()
    return jsonify({'ok': True})


# ──────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    HOST, PORT = '0.0.0.0', 8080
    lan = _lan_ip()
    print('=' * 48)
    print(f'  XploraVentures Dashboard  v{VERSION}')
    print(f'  http://localhost:{PORT}')
    print(f'  http://{lan}:{PORT}  ← share on LAN')
    print('=' * 48)
    threading.Thread(target=_check_for_update, daemon=True).start()
    webbrowser.open(f'http://localhost:{PORT}')
    socketio.run(app, host=HOST, port=PORT, use_reloader=False)
