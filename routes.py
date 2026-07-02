import os
import socket as _socket

from flask import Blueprint, request, jsonify, send_file

from serial_manager import SerialManager
from cycle_runner import CycleRunner
from logger import CsvLogger


def create_blueprint(
    serial_mgr: SerialManager,
    cycle_runner: CycleRunner,
    csv_logger: CsvLogger,
    db,               # SupabaseDB | None
    run_id_holder: dict,  # {"id": None, "last_phase": "idle"} — shared with main
) -> Blueprint:
    bp = Blueprint("api", __name__)

    # ── Serial ────────────────────────────────────────────────────────────────

    @bp.route("/api/ports")
    def api_ports():
        return jsonify(serial_mgr.list_ports())

    @bp.route("/api/connect", methods=["POST"])
    def api_connect():
        body = request.json or {}
        port = body.get("port", "")
        baud = int(body.get("baud", 115200))
        try:
            serial_mgr.connect(port, baud)
            return jsonify({"ok": True})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)})

    @bp.route("/api/disconnect", methods=["POST"])
    def api_disconnect():
        serial_mgr.disconnect()
        return jsonify({"ok": True})

    @bp.route("/api/command", methods=["POST"])
    def api_command():
        serial_mgr.send(request.json or {})
        return jsonify({"ok": True})

    # ── CSV Logging ───────────────────────────────────────────────────────────

    @bp.route("/api/log/start", methods=["POST"])
    def api_log_start():
        ok, result = csv_logger.start()
        if ok:
            return jsonify({"ok": True, "file": result})
        return jsonify({"ok": False, "error": result})

    @bp.route("/api/log/stop", methods=["POST"])
    def api_log_stop():
        csv_logger.stop()
        return jsonify({"ok": True})

    @bp.route("/api/log/download")
    def api_log_download():
        path = csv_logger.path
        if path and os.path.exists(path):
            return send_file(path, as_attachment=True)
        return jsonify({"error": "no log"}), 404

    # ── Cycle ─────────────────────────────────────────────────────────────────

    @bp.route("/api/cycle/start", methods=["POST"])
    def api_cycle_start():
        body = request.json or {}
        params = {
            "charge_sp":         float(body.get("charge_sp",         120)),
            "regen_duration_min": float(body.get("regen_duration_min", 30)),
            "num_cycles":     int(body.get("num_cycles",        1)),
            "discharge_dh":   float(body.get("discharge_dh",    1.5)),
            "cooldown_dt":    float(body.get("cooldown_dt",    2.0)),
            "min_discharge_s": int(body.get("min_discharge_s", 600)),
            "start_phase":    body.get("start_phase", "discharging"),
            "dry_weight":      float(body["dry_weight"])      if body.get("dry_weight")      else None,
            "flow_discharge":  float(body["flow_discharge"])  if body.get("flow_discharge")  else None,
            "flow_charge":     float(body["flow_charge"])     if body.get("flow_charge")     else None,
        }
        if db:
            run_id_holder["id"] = db.start_cycle_run(params)
            run_id_holder["last_phase"] = "idle"
        ok, err = cycle_runner.start(**params)
        if not ok:
            return jsonify({"ok": False, "error": err})
        log_ok, log_result = csv_logger.start()
        log_file = log_result if log_ok else csv_logger.path
        return jsonify({"ok": True, "log_file": log_file})

    @bp.route("/api/cycle/update", methods=["POST"])
    def api_cycle_update():
        body = request.json or {}
        params = {}
        if "charge_sp"          in body: params["charge_sp"]          = float(body["charge_sp"])
        if "regen_duration_min" in body: params["regen_duration_min"] = float(body["regen_duration_min"])
        if "num_cycles"        in body: params["num_cycles"]         = int(body["num_cycles"])
        if "discharge_dh"      in body: params["discharge_dh"]      = float(body["discharge_dh"])
        if "cooldown_dt"       in body: params["cooldown_dt"]        = float(body["cooldown_dt"])
        if "wet_weight_g"      in body: params["wet_weight_g"]      = float(body["wet_weight_g"])
        if "post_dry_weight_g" in body: params["post_dry_weight_g"] = float(body["post_dry_weight_g"])
        cycle_runner.update_params(**params)
        return jsonify({"ok": True})

    @bp.route("/api/cycle/stop", methods=["POST"])
    def api_cycle_stop():
        cycle_runner.stop()
        return jsonify({"ok": True})

    @bp.route("/api/cycle/pause", methods=["POST"])
    def api_cycle_pause():
        cycle_runner.pause()
        return jsonify({"ok": True})

    @bp.route("/api/cycle/resume", methods=["POST"])
    def api_cycle_resume():
        cycle_runner.resume()
        return jsonify({"ok": True})

    # ── Misc ──────────────────────────────────────────────────────────────────

    @bp.route("/api/ip")
    def api_ip():
        try:
            s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
        except Exception:
            ip = "127.0.0.1"
        return jsonify({"ip": ip, "port": 8080})

    return bp
