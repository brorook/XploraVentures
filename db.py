import datetime
import threading

from supabase import create_client, Client


class SupabaseDB:
    """Thin wrapper around the Supabase client for reactor data persistence.

    Tables expected (see supabase_setup.sql):
      telemetry    — one row per sensor reading (throttled to ~10 s interval)
      cycle_runs   — one row per cycle run (params + outcome)
      cycle_events — one row per phase transition within a run
    """

    def __init__(self, url: str, key: str):
        self._client: Client = create_client(url, key)
        self._lock = threading.Lock()

    # ── Telemetry ─────────────────────────────────────────────────────────────

    def insert_telemetry(self, data: dict, run_id: int | None = None):
        row = {
            "ts":        datetime.datetime.utcnow().isoformat(),
            "run_id":    run_id,
            "ch1_t":     data.get("sht1", {}).get("t"),
            "ch1_h":     data.get("sht1", {}).get("h"),
            "ch3_t":     data.get("sht3", {}).get("t"),
            "ch3_h":     data.get("sht3", {}).get("h"),
            "heater":    bool(data.get("heater",    False)),
            "solenoid":  bool(data.get("solenoid",  False)),
            "solenoid2": bool(data.get("solenoid2", False)),
            "setpoint":  data.get("setpoint"),
        }
        self._insert("telemetry", row)

    # ── Cycle runs ────────────────────────────────────────────────────────────

    def start_cycle_run(self, params: dict) -> int | None:
        row = {
            "started_at":     datetime.datetime.utcnow().isoformat(),
            "charge_sp":      params.get("charge_sp"),
            "charge_dur_s":   params.get("charge_dur_s"),
            "num_cycles":     params.get("num_cycles"),
            "discharge_dh":   params.get("discharge_dh"),
            "cooldown_dt":    params.get("cooldown_dt"),
            "dry_weight_g":   params.get("dry_weight"),
            "flow_discharge": params.get("flow_discharge"),
            "flow_charge":    params.get("flow_charge"),
        }
        try:
            with self._lock:
                res = self._client.table("cycle_runs").insert(row).execute()
            return res.data[0]["id"] if res.data else None
        except Exception:
            return None

    def end_cycle_run(self, run_id: int | None, outcome: str):
        if run_id is None:
            return
        self._update("cycle_runs", {"ended_at": datetime.datetime.utcnow().isoformat(), "outcome": outcome}, "id", run_id)

    # ── Cycle events ──────────────────────────────────────────────────────────

    def insert_cycle_event(self, run_id: int | None, phase: str, cycle_num: int, elapsed_s: int):
        if run_id is None:
            return
        row = {
            "run_id":    run_id,
            "ts":        datetime.datetime.utcnow().isoformat(),
            "phase":     phase,
            "cycle_num": cycle_num,
            "elapsed_s": elapsed_s,
        }
        self._insert("cycle_events", row)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _insert(self, table: str, row: dict):
        try:
            with self._lock:
                self._client.table(table).insert(row).execute()
        except Exception:
            pass

    def _update(self, table: str, values: dict, pk_col: str, pk_val):
        try:
            with self._lock:
                self._client.table(table).update(values).eq(pk_col, pk_val).execute()
        except Exception:
            pass
