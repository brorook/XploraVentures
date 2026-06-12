import csv
import datetime
import os
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

    def start(self) -> tuple[bool, str]:
        with self._lock:
            if self._writer:
                return False, "already logging"
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            self._path = f"accel_reactor_{ts}.csv"
            self._file = open(self._path, "w", newline="")
            self._writer = csv.writer(self._file)
            self._writer.writerow([
                "timestamp", "ch1_t", "ch1_h", "ch3_t", "ch3_h",
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
                data.get("sht1", {}).get("t", ""),
                data.get("sht1", {}).get("h", ""),
                data.get("sht3", {}).get("t", ""),
                data.get("sht3", {}).get("h", ""),
                int(data.get("heater",    False)),
                int(data.get("solenoid",  False)),
                int(data.get("solenoid2", False)),
                data.get("setpoint", ""),
            ])
            self._file.flush()
