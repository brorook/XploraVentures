import csv
import datetime
import os
import re
import threading


class CsvLogger:
    def __init__(self):
        self._file = None
        self._writer = None
        self._path = None
        self._lock = threading.Lock()

    @property
    def path(self) -> str | None:
        return self._path

    _DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "experimental data")

    def _next_run_number(self) -> int:
        if not os.path.isdir(self._DATA_DIR):
            return 1
        nums = [
            int(m.group(1))
            for name in os.listdir(self._DATA_DIR)
            if (m := re.match(r"ACT_(\d+)_", name))
        ]
        return max(nums, default=0) + 1

    def start(self) -> tuple[bool, str]:
        with self._lock:
            if self._writer:
                return False, "already logging"
            os.makedirs(self._DATA_DIR, exist_ok=True)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            run = self._next_run_number()
            self._path = os.path.join(self._DATA_DIR, f"ACT_{run:03d}_3LPM_{ts}_.csv")
            self._file = open(self._path, "w", newline="")
            self._writer = csv.writer(self._file)
            self._writer.writerow([
                "timestamp", "phase",
                "ch1_t", "ch1_h",
                "ch3_t", "ch3_h",
                "rtd_t",
                "flow_slpm",
                "heater", "drier", "humidifier", "setpoint",
            ])
        return True, self._path

    def stop(self):
        with self._lock:
            if self._file:
                self._file.close()
            self._file = self._writer = None

    def log_row(self, data: dict):
        with self._lock:
            if self._writer is None:
                return
            self._writer.writerow([
                datetime.datetime.now().isoformat(),
                data.get("_phase", ""),
                data.get("sht1", {}).get("t", ""), data.get("sht1", {}).get("h", ""),
                data.get("sht3", {}).get("t", ""), data.get("sht3", {}).get("h", ""),
                data.get("rtd", ""),
                data.get("flow_slpm", ""),
                int(data.get("heater",    False)),
                int(data.get("solenoid",  False)),
                int(data.get("solenoid2", False)),
                data.get("setpoint", ""),
            ])
            self._file.flush()
