"""
Accelerated Reactor Dashboard — entry point.
Flask + SocketIO dashboard for the AcceleratedReactor firmware.
Two SHT45 sensors (Ch1, Ch3), coil heater on MOSFET CH0, humidifier on CH1, drier on CH2.
"""

__version__ = "1.0.0"
_RELEASES_API = "https://api.github.com/repos/brorook/XploraVentures/releases/latest"

import os
import json
import time
import threading
import webbrowser
import urllib.request

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from flask import Flask, render_template
from flask_socketio import SocketIO

from serial_manager import SerialManager
from cycle_runner import CycleRunner
from logger import CsvLogger
from routes import create_blueprint

# ─── App ──────────────────────────────────────────────────────────────────────

app = Flask(__name__, template_folder="templates")
socketio = SocketIO(app, cors_allowed_origins="*")

# ─── Subsystems ───────────────────────────────────────────────────────────────

serial_mgr = SerialManager()
csv_logger = CsvLogger()

db = None
_sb_url = os.getenv("SUPABASE_URL")
_sb_key = os.getenv("SUPABASE_ANON_KEY")
if _sb_url and _sb_key:
    try:
        from db import SupabaseDB
        db = SupabaseDB(_sb_url, _sb_key)
        print(f"Supabase connected: {_sb_url}")
    except Exception as e:
        print(f"Supabase init failed: {e}")

# ─── Cycle DB tracking ────────────────────────────────────────────────────────

# Shared with routes.py so /api/cycle/start can write the run_id
run_id_holder = {"id": None, "last_phase": "idle"}

def _on_cycle_status(status: dict):
    socketio.emit("cycle_status", status)
    if db is None:
        return
    phase = status["phase"]
    if phase == run_id_holder["last_phase"]:
        return
    run_id_holder["last_phase"] = phase
    run_id = run_id_holder["id"]
    if phase not in ("idle", "done", "stopped"):
        threading.Thread(
            target=db.insert_cycle_event,
            args=(run_id, phase, status.get("cycle", 0), status.get("elapsed_s", 0)),
            daemon=True,
        ).start()
    elif phase in ("done", "stopped"):
        threading.Thread(target=db.end_cycle_run, args=(run_id, phase), daemon=True).start()
        run_id_holder["id"] = None

cycle_runner = CycleRunner(send_fn=serial_mgr.send, on_status=_on_cycle_status)

# ─── Serial telemetry ─────────────────────────────────────────────────────────

_last_db_telemetry = 0.0

def _on_telemetry(data: dict):
    global _last_db_telemetry
    socketio.emit("telemetry", data)
    csv_logger.log_row(data)
    if "sht1" in data:
        cycle_runner.last_t1 = data["sht1"].get("t")
    if "sht3" in data:
        cycle_runner.last_t3 = data["sht3"].get("t")
    if db:
        now = time.monotonic()
        if now - _last_db_telemetry >= 10.0:  # throttle to ~6/min to stay within Supabase free tier
            _last_db_telemetry = now
            threading.Thread(target=db.insert_telemetry, args=(data,), daemon=True).start()

serial_mgr.add_listener(_on_telemetry)

# ─── Routes ───────────────────────────────────────────────────────────────────

app.register_blueprint(
    create_blueprint(serial_mgr, cycle_runner, csv_logger, db, run_id_holder)
)

@app.route("/")
def index():
    return render_template("index.html")

# ─── Update check ─────────────────────────────────────────────────────────────

def _update_checker():
    time.sleep(4)
    try:
        req = urllib.request.Request(_RELEASES_API, headers={"User-Agent": "AcceleratedReactor"})
        with urllib.request.urlopen(req, timeout=5) as r:
            tag = json.loads(r.read())["tag_name"].lstrip("v")
        if tag != __version__:
            socketio.emit("update_available", {"version": tag, "current": __version__})
    except Exception:
        pass

# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    threading.Thread(target=_update_checker, daemon=True).start()
    webbrowser.open("http://localhost:8080")
    socketio.run(app, host="0.0.0.0", port=8080, debug=False, allow_unsafe_werkzeug=True)
