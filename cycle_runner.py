import threading
import time
from typing import Callable


class CycleRunner:
    def __init__(self, send_fn: Callable, on_status: Callable):
        self._send = send_fn
        self._on_status = on_status
        self._thread = None
        self._stop_evt = threading.Event()
        self._lock = threading.Lock()
        self._status = {
            "phase": "idle", "cycle": 0, "total": 0,
            "elapsed_s": 0, "delta_t_live": 0.0, "params": {},
        }
        # Updated by serial listener in main
        self.last_t1 = None
        self.last_t3 = None

    def start(self, charge_sp: float, charge_dur_s: int, cool_to: float,
              delta_t: float, num_cycles: int, start_phase: str = "charge"):
        if self._thread and self._thread.is_alive():
            return False, "already running"
        self._stop_evt.clear()
        self._thread = threading.Thread(
            target=self._run,
            kwargs=dict(charge_sp=charge_sp, charge_dur_s=charge_dur_s,
                        cool_to=cool_to, delta_t=delta_t,
                        num_cycles=num_cycles, start_phase=start_phase),
            daemon=True,
        )
        self._thread.start()
        return True, None

    def stop(self):
        self._stop_evt.set()

    def get_status(self) -> dict:
        with self._lock:
            return dict(self._status)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _emit(self):
        self._on_status(self.get_status())

    def _set_phase(self, phase: str, cycle: int | None = None):
        with self._lock:
            self._status["phase"] = phase
            if cycle is not None:
                self._status["cycle"] = cycle
        self._emit()

    def _run(self, charge_sp, charge_dur_s, cool_to, delta_t, num_cycles, start_phase):
        with self._lock:
            self._status.update({
                "total": num_cycles, "elapsed_s": 0, "delta_t_live": 0.0,
                "params": {"charge_dur_s": charge_dur_s, "delta_t": delta_t},
            })

        skip_charge = (start_phase == "discharge")

        for n in range(1, num_cycles + 1):
            if self._stop_evt.is_set():
                break

            if not skip_charge:
                # CHARGE ──────────────────────────────────────────────────────
                self._set_phase("charging", n)
                self._send({"cmd": "solenoid2", "on": True})
                self._send({"cmd": "solenoid",  "on": False})
                self._send({"cmd": "set_sp",    "val": charge_sp})
                t0 = time.monotonic()
                while not self._stop_evt.is_set():
                    elapsed = time.monotonic() - t0
                    with self._lock:
                        self._status["elapsed_s"] = int(elapsed)
                    self._emit()
                    if elapsed >= charge_dur_s:
                        break
                    self._stop_evt.wait(2.0)

                if self._stop_evt.is_set():
                    break

                # COOLDOWN ────────────────────────────────────────────────────
                self._set_phase("cooling", n)
                self._send({"cmd": "solenoid2", "on": True})
                self._send({"cmd": "solenoid",  "on": False})
                self._send({"cmd": "set_sp",    "val": 0})
                while not self._stop_evt.is_set():
                    t3 = self.last_t3
                    if t3 is not None and t3 <= cool_to:
                        break
                    self._stop_evt.wait(2.0)

                if self._stop_evt.is_set():
                    break

            skip_charge = False

            # DISCHARGE ───────────────────────────────────────────────────────
            self._set_phase("discharging", n)
            self._send({"cmd": "solenoid",  "on": True})
            self._send({"cmd": "solenoid2", "on": False})
            peaked = False
            t_dis = time.monotonic()
            while not self._stop_evt.is_set():
                t3   = self.last_t3
                t1   = self.last_t1
                diff = round(t3 - t1, 1) if (t3 is not None and t1 is not None) else 0.0
                with self._lock:
                    self._status["elapsed_s"]    = int(time.monotonic() - t_dis)
                    self._status["delta_t_live"] = diff
                self._emit()
                if diff > delta_t * 2:
                    peaked = True
                if peaked and diff <= delta_t:
                    break
                if time.monotonic() - t_dis > 21600:  # 6-hour safety timeout
                    break
                self._stop_evt.wait(2.0)

            self._send({"cmd": "solenoid",  "on": False})
            self._send({"cmd": "solenoid2", "on": False})

            if self._stop_evt.is_set():
                break

        # Finish ──────────────────────────────────────────────────────────────
        self._send({"cmd": "solenoid",  "on": False})
        self._send({"cmd": "solenoid2", "on": False})
        self._send({"cmd": "set_sp",    "val": 0})
        with self._lock:
            self._status["phase"]        = "stopped" if self._stop_evt.is_set() else "done"
            self._status["cycle"]        = 0
            self._status["elapsed_s"]    = 0
            self._status["delta_t_live"] = 0.0
        self._emit()
