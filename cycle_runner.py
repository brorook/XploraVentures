import math
import threading
import time
from typing import Callable


def _abs_humidity(t_c: float, rh: float) -> float:
    """Absolute humidity in g/m³ (Sensirion coefficients)."""
    rh = max(0.0, min(rh, 100.0))
    return 216.7 * (rh / 100.0 * 6.112 * math.exp(17.62 * t_c / (243.12 + t_c))) / (273.15 + t_c)


def _mixing_ratio(t_c: float, rh: float, p_hpa: float = 1013.25) -> float:
    """Mixing ratio in g/kg dry air (Sensirion coefficients, assumes standard pressure)."""
    rh = max(0.0, min(rh, 100.0))
    e = rh / 100.0 * 6.112 * math.exp(17.62 * t_c / (243.12 + t_c))
    return 622.0 * e / (p_hpa - e)


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
            "mass_flux_g_min": 0.0, "water_absorbed_g": 0.0, "water_released_g": 0.0,
            "regen_energy_wh": 0.0,
        }
        self.last_t1 = None
        self.last_t3 = None
        self.last_h1 = None
        self.last_h3 = None
        self.last_rtd = None
        self.last_heater = False
        self._paused_phase = None
        self._current_charge_sp = 0.0
        self._regen_end_dh = 1.0
        self._num_cycles = 0
        self._discharge_dh = 0.0
        self._cooldown_dt = 0.0
        self._dry_weight = None
        self._wet_weight_g = None
        self._post_dry_weight_g = None
        self._flow_discharge = None
        self._flow_charge = None
        self._heater_voltage = None
        self._heater_current = None

    def start(self, charge_sp: float, regen_end_dh: float, num_cycles: int,
              discharge_dh: float = 1.5, cooldown_dt: float = 2.0,
              min_discharge_s: int = 600,
              dry_weight: float = None, flow_discharge: float = None, flow_charge: float = None,
              heater_voltage: float = None, heater_current: float = None,
              start_phase: str = "discharging"):
        if self._thread and self._thread.is_alive():
            return False, "already running"
        self._current_charge_sp = charge_sp
        self._regen_end_dh      = regen_end_dh
        self._num_cycles        = num_cycles
        self._discharge_dh      = discharge_dh
        self._cooldown_dt       = cooldown_dt
        self._dry_weight        = dry_weight
        self._flow_discharge    = flow_discharge
        self._flow_charge       = flow_charge
        self._heater_voltage    = heater_voltage
        self._heater_current    = heater_current
        self._min_discharge_s   = min_discharge_s
        self._stop_evt.clear()
        self._pause_evt.clear()
        self._thread = threading.Thread(
            target=self._run,
            kwargs=dict(charge_sp=charge_sp, regen_end_dh=regen_end_dh, num_cycles=num_cycles,
                        discharge_dh=discharge_dh, cooldown_dt=cooldown_dt,
                        min_discharge_s=min_discharge_s, start_phase=start_phase),
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
        elif phase == "regenerating":
            self._send({"cmd": "solenoid2", "on": True})
            self._send({"cmd": "set_sp",    "val": self._current_charge_sp})
        elif phase == "cooling":
            self._send({"cmd": "solenoid",  "on": False})
            self._send({"cmd": "solenoid2", "on": True})
        with self._lock:
            self._status["phase"] = phase
        self._pause_evt.clear()
        self._emit()

    def update_params(self, charge_sp=None, regen_end_dh=None, num_cycles=None,
                      discharge_dh=None, cooldown_dt=None,
                      wet_weight_g=None, post_dry_weight_g=None):
        if charge_sp is not None:
            self._current_charge_sp = float(charge_sp)
            if self._status["phase"] == "regenerating":
                self._send({"cmd": "set_sp", "val": self._current_charge_sp})
        if regen_end_dh is not None:
            self._regen_end_dh = float(regen_end_dh)
        if num_cycles is not None:
            self._num_cycles = max(1, int(num_cycles))
            with self._lock:
                self._status["total"] = self._num_cycles
        if discharge_dh is not None:
            self._discharge_dh = float(discharge_dh)
        if cooldown_dt is not None:
            self._cooldown_dt = float(cooldown_dt)
        if wet_weight_g is not None:
            self._wet_weight_g = float(wet_weight_g)
        if post_dry_weight_g is not None:
            self._post_dry_weight_g = float(post_dry_weight_g)
        with self._lock:
            self._status["params"] = {
                "charge_sp":    self._current_charge_sp,
                "regen_end_dh": self._regen_end_dh,
                "discharge_dh": self._discharge_dh,
                "cooldown_dt":  self._cooldown_dt,
            }
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

    def _run(self, charge_sp, regen_end_dh, num_cycles, discharge_dh, cooldown_dt, min_discharge_s=600, start_phase="discharging"):
        with self._lock:
            self._status.update({
                "total": num_cycles, "elapsed_s": 0,
                "delta_t_live": 0.0, "delta_h_live": 0.0,
                "params": {"charge_sp": self._current_charge_sp, "regen_end_dh": self._regen_end_dh,
                           "discharge_dh": self._discharge_dh, "cooldown_dt": self._cooldown_dt},
            })

        n = 0
        while not self._stop_evt.is_set():
            n += 1
            if n > self._num_cycles:
                break

            # DISCHARGE ───────────────────────────────────────────────────────
            if not (n == 1 and start_phase in ("regenerating", "cooling")):
                # Ends after min_discharge_s when inlet-outlet AH delta <= discharge_dh (bed saturated)
                self._set_phase("discharging", n)
                self._send({"cmd": "solenoid",  "on": True})
                self._send({"cmd": "solenoid2", "on": False})
                self._send({"cmd": "set_sp",    "val": 0})
                with self._lock:
                    self._status["water_absorbed_g"] = 0.0
                    self._status["mass_flux_g_min"]  = 0.0
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
                        if self._flow_discharge:
                            flux = self._flow_discharge * max(0.0, ah1 - ah3) / 1000.0
                            with self._lock:
                                self._status["water_absorbed_g"] += flux * (2.0 / 60.0)
                                self._status["mass_flux_g_min"]   = round(flux, 4)
                    else:
                        ah1 = ah3 = None
                        delta_h = 0.0
                    elapsed = time.monotonic() - t_dis - pause_offset
                    with self._lock:
                        self._status["elapsed_s"]    = int(elapsed)
                        self._status["delta_h_live"] = delta_h
                    self._emit()
                    if have_all and elapsed >= self._min_discharge_s and ah1 - ah3 <= self._discharge_dh:
                        break
                    if elapsed > 21600:  # 6-hour safety timeout
                        break
                    pause_offset += self._tick()

                self._send({"cmd": "solenoid", "on": False})
                if self._stop_evt.is_set():
                    break

            # REGENERATION ────────────────────────────────────────────────────
            # Ends when RTD >= charge_sp AND outlet-inlet AH delta <= regen_end_dh
            if not (n == 1 and start_phase == "cooling"):
                self._set_phase("regenerating", n)
                with self._lock:
                    self._status["elapsed_s"]        = 0
                    self._status["delta_h_live"]     = 0.0
                    self._status["water_released_g"] = 0.0
                    self._status["mass_flux_g_min"]  = 0.0
                self._send({"cmd": "solenoid2", "on": True})
                self._send({"cmd": "set_sp",    "val": self._current_charge_sp})
                t_regen = time.monotonic()
                pause_offset = 0.0
                with self._lock:
                    self._status["regen_energy_wh"] = 0.0
                while not self._stop_evt.is_set():
                    t1, h1 = self.last_t1, self.last_h1
                    t3, h3 = self.last_t3, self.last_h3
                    rtd = self.last_rtd
                    elapsed = int(time.monotonic() - t_regen - pause_offset)
                    have_all = all(v is not None for v in (t1, h1, t3, h3))
                    if have_all:
                        ah1 = _abs_humidity(t1, h1)
                        ah3 = _abs_humidity(t3, h3)
                        delta_h = round(ah3 - ah1, 2)
                        if self._flow_charge:
                            flux = self._flow_charge * max(0.0, ah3 - ah1) / 1000.0
                            with self._lock:
                                self._status["water_released_g"] += flux * (2.0 / 60.0)
                                self._status["mass_flux_g_min"]   = round(flux, 4)
                    else:
                        ah1 = ah3 = None
                        delta_h = 0.0
                    with self._lock:
                        self._status["elapsed_s"]    = elapsed
                        self._status["delta_h_live"] = delta_h
                    # Accumulate heater energy: sample is 2 s; only count when heater is actually ON
                    if self._heater_voltage and self._heater_current and self.last_heater:
                        with self._lock:
                            self._status["regen_energy_wh"] += self._heater_voltage * self._heater_current * 2.0 / 3600.0
                    self._emit()
                    if have_all and rtd is not None and rtd >= self._current_charge_sp and delta_h <= self._regen_end_dh:
                        break
                    if elapsed > 21600:  # 6-hour safety timeout
                        break
                    paused_s = self._tick()
                    pause_offset += paused_s

                if self._stop_evt.is_set():
                    break

            # COOLDOWN ────────────────────────────────────────────────────────
            # Ends when RTD temp <= inlet temp (T1) + cooldown_dt
            self._set_phase("cooling", n)
            with self._lock:
                self._status["mass_flux_g_min"] = 0.0
            self._send({"cmd": "solenoid",  "on": False})
            self._send({"cmd": "solenoid2", "on": True})
            self._send({"cmd": "set_sp",    "val": 0})
            while not self._stop_evt.is_set():
                t1  = self.last_t1
                rtd = self.last_rtd
                diff = round(rtd - t1, 1) if (t1 is not None and rtd is not None) else 0.0
                with self._lock:
                    self._status["delta_t_live"] = diff
                self._emit()
                if t1 is not None and rtd is not None and rtd <= t1 + self._cooldown_dt:
                    break
                self._tick()

            if self._stop_evt.is_set():
                break

        # Finish ──────────────────────────────────────────────────────────────
        self._send({"cmd": "solenoid",  "on": False})
        self._send({"cmd": "solenoid2", "on": False})
        self._send({"cmd": "set_sp",    "val": 0})
        with self._lock:
            self._status["phase"]           = "stopped" if self._stop_evt.is_set() else "done"
            self._status["cycle"]           = 0
            self._status["elapsed_s"]       = 0
            self._status["delta_t_live"]    = 0.0
            self._status["delta_h_live"]    = 0.0
            self._status["mass_flux_g_min"] = 0.0
        self._emit()
