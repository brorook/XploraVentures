import math
import threading
import time
from typing import Callable


def _abs_humidity(t_c: float, rh: float) -> float:
    """Absolute humidity in g/m³ from temperature (°C) and relative humidity (%)."""
    return 216.7 * (rh / 100.0 * 6.112 * math.exp(17.67 * t_c / (t_c + 243.5))) / (273.15 + t_c)


class CycleRunner:
    def __init__(self, send_fn: Callable, on_status: Callable):
        self._send = send_fn
        self._on_status = on_status
        self._thread = None
        self._stop_evt = threading.Event()
        self._pause_evt = threading.Event()   # set = paused
        self._lock = threading.Lock()
        self._status = {
            "phase": "idle", "cycle": 0, "total": 0,
            "elapsed_s": 0, "delta_t_live": 0.0, "delta_h_live": 0.0, "params": {},
        }
        self.last_t1 = None
        self.last_t3 = None
        self.last_h1 = None
        self.last_h3 = None
        self._paused_phase = None
        self._current_charge_sp = 0.0

    def start(self, charge_sp: float, charge_dur_s: int, num_cycles: int,
              discharge_dh: float = 1.5, cooldown_dt: float = 2.0):
        if self._thread and self._thread.is_alive():
            return False, "already running"
        self._stop_evt.clear()
        self._pause_evt.clear()
        self._thread = threading.Thread(
            target=self._run,
            kwargs=dict(charge_sp=charge_sp, charge_dur_s=charge_dur_s, num_cycles=num_cycles,
                        discharge_dh=discharge_dh, cooldown_dt=cooldown_dt),
            daemon=True,
        )
        self._thread.start()
        return True, None

    def stop(self):
        self._stop_evt.set()
        self._pause_evt.clear()  # unblock any pause-wait so the thread can exit

    def pause(self):
        with self._lock:
            phase = self._status["phase"]
            if phase in ("idle", "done", "stopped", "paused"):
                return
            self._paused_phase = phase
            self._status["phase"] = "paused"
        self._send({"cmd": "solenoid",  "on": False})
        self._send({"cmd": "solenoid2", "on": False})
        self._send({"cmd": "set_sp",    "val": 0})
        self._pause_evt.set()
        self._emit()

    def resume(self):
        if not self._pause_evt.is_set():
            return
        phase = self._paused_phase
        if phase == "discharging":
            self._send({"cmd": "solenoid",  "on": True})
            self._send({"cmd": "solenoid2", "on": False})
            self._send({"cmd": "set_sp",    "val": 0})
        elif phase == "charging":
            self._send({"cmd": "solenoid2", "on": True})
            self._send({"cmd": "set_sp",    "val": self._current_charge_sp})
        # cooling: actuators are already off — nothing to re-activate
        with self._lock:
            self._status["phase"] = phase
        self._pause_evt.clear()
        self._emit()

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

    def _tick(self) -> float:
        """Sleep 2 s, blocking through any pause. Returns seconds spent paused."""
        self._stop_evt.wait(2.0)
        if not self._pause_evt.is_set():
            return 0.0
        t0 = time.monotonic()
        while self._pause_evt.is_set() and not self._stop_evt.is_set():
            time.sleep(0.2)
        return time.monotonic() - t0

    def _run(self, charge_sp, charge_dur_s, num_cycles, discharge_dh, cooldown_dt):
        self._current_charge_sp = charge_sp
        with self._lock:
            self._status.update({
                "total": num_cycles, "elapsed_s": 0,
                "delta_t_live": 0.0, "delta_h_live": 0.0,
                "params": {"charge_sp": charge_sp, "charge_dur_s": charge_dur_s,
                           "discharge_dh": discharge_dh, "cooldown_dt": cooldown_dt},
            })

        for n in range(1, num_cycles + 1):
            if self._stop_evt.is_set():
                break

            # DISCHARGE ───────────────────────────────────────────────────────
            # Ends when outlet humidity (H3) ≤ inlet humidity (H1) − discharge_dh g/m³
            self._set_phase("discharging", n)
            self._send({"cmd": "solenoid",  "on": True})
            self._send({"cmd": "solenoid2", "on": False})
            self._send({"cmd": "set_sp",    "val": 0})
            t_dis = time.monotonic()
            pause_offset = 0.0
            while not self._stop_evt.is_set():
                t1, h1 = self.last_t1, self.last_h1
                t3, h3 = self.last_t3, self.last_h3
                have_all = all(v is not None for v in (t1, h1, t3, h3))
                if have_all:
                    ah1 = _abs_humidity(t1, h1)
                    ah3 = _abs_humidity(t3, h3)
                    delta_h = round(ah3 - ah1, 2)
                else:
                    ah1 = ah3 = None
                    delta_h = 0.0
                elapsed = time.monotonic() - t_dis - pause_offset
                with self._lock:
                    self._status["elapsed_s"]    = int(elapsed)
                    self._status["delta_h_live"] = delta_h
                self._emit()
                if have_all and ah3 <= ah1 - discharge_dh:
                    break
                if elapsed > 21600:  # 6-hour safety timeout
                    break
                pause_offset += self._tick()

            self._send({"cmd": "solenoid", "on": False})
            if self._stop_evt.is_set():
                break

            # CHARGE ──────────────────────────────────────────────────────────
            # Ends when T3 ≥ charge_sp AND sustained continuously for charge_dur_s
            self._set_phase("charging", n)
            with self._lock:
                self._status["elapsed_s"] = 0
            self._send({"cmd": "solenoid2", "on": True})
            self._send({"cmd": "set_sp",    "val": charge_sp})
            at_temp_since = None
            while not self._stop_evt.is_set():
                t3 = self.last_t3
                if t3 is not None:
                    if t3 >= charge_sp:
                        if at_temp_since is None:
                            at_temp_since = time.monotonic()
                        elapsed_at = int(time.monotonic() - at_temp_since)
                        with self._lock:
                            self._status["elapsed_s"] = elapsed_at
                        self._emit()
                        if elapsed_at >= charge_dur_s:
                            break
                    else:
                        if at_temp_since is not None:
                            at_temp_since = None
                            with self._lock:
                                self._status["elapsed_s"] = 0
                            self._emit()
                paused_s = self._tick()
                if at_temp_since is not None:
                    at_temp_since += paused_s  # freeze the soak timer while paused

            if self._stop_evt.is_set():
                break

            # COOLDOWN ────────────────────────────────────────────────────────
            # Ends when outlet temp (T3) ≤ inlet temp (T1) − 2 °C
            self._set_phase("cooling", n)
            self._send({"cmd": "solenoid2", "on": False})
            self._send({"cmd": "set_sp",    "val": 0})
            while not self._stop_evt.is_set():
                t1 = self.last_t1
                t3 = self.last_t3
                diff = round(t3 - t1, 1) if (t1 is not None and t3 is not None) else 0.0
                with self._lock:
                    self._status["delta_t_live"] = diff
                self._emit()
                if t1 is not None and t3 is not None and t3 <= t1 - cooldown_dt:
                    break
                self._tick()

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
            self._status["delta_h_live"] = 0.0
        self._emit()
