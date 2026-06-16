"""
Offline viewer for accelerated-reactor CSV logs in DATA/.

Controls
--------
  Radio buttons  : switch between session files
  Checkboxes     : toggle individual series
  Mouse scroll   : zoom x-axis (matplotlib built-in)
"""

import os
import glob
import csv
import datetime
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.widgets import RadioButtons, CheckButtons
from matplotlib.patches import Patch
import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "DATA")

COLOR_CH1_T  = "#e05252"
COLOR_CH3_T  = "#e08c52"
COLOR_CH1_H  = "#5285e0"
COLOR_CH3_H  = "#52c4e0"
COLOR_HEATER = "#ffaaaa"
COLOR_DRIER  = "#aaffaa"
COLOR_HUMID  = "#aaaaff"


def load_csv(path):
    ts, ch1_t, ch1_h, ch3_t, ch3_h = [], [], [], [], []
    heater, drier, humidifier, setpoint = [], [], [], []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                ts.append(datetime.datetime.fromisoformat(row["timestamp"]))
                ch1_t.append(float(row["ch1_t"]))
                ch1_h.append(float(row["ch1_h"]))
                ch3_t.append(float(row["ch3_t"]))
                ch3_h.append(float(row["ch3_h"]))
                heater.append(int(row["heater"]))
                drier.append(int(row["drier"]))
                humidifier.append(int(row["humidifier"]))
                setpoint.append(float(row["setpoint"]))
            except (ValueError, KeyError):
                continue
    return {
        "ts": ts, "ch1_t": ch1_t, "ch1_h": ch1_h,
        "ch3_t": ch3_t, "ch3_h": ch3_h,
        "heater": heater, "drier": drier,
        "humidifier": humidifier, "setpoint": setpoint,
    }


def _spans(ts, flag):
    """Return list of (start, end) datetime pairs where flag==1."""
    spans = []
    start = None
    for t, v in zip(ts, flag):
        if v and start is None:
            start = t
        elif not v and start is not None:
            spans.append((start, t))
            start = None
    if start is not None and ts:
        spans.append((start, ts[-1]))
    return spans


def label_from_path(path):
    name = os.path.basename(path)
    # accel_reactor_YYYYMMDD_HHMMSS.csv  →  Jun 10  10:51
    try:
        stem = name.replace("accel_reactor_", "").replace(".csv", "")
        for fmt, out in [("%Y%m%d_%H%M%S", "%b %d  %H:%M"), ("%Y%m%d", "%b %d")]:
            try:
                return datetime.datetime.strptime(stem, fmt).strftime(out)
            except ValueError:
                continue
    except Exception:
        pass
    return name


class Viewer:
    def __init__(self, files):
        self.files = files
        self.labels = [label_from_path(f) for f in files]
        self.cache = {}
        self.current = 0

        # ── layout ────────────────────────────────────────────────────────────
        self.fig = plt.figure(figsize=(14, 7))
        self.fig.patch.set_facecolor("#1e1e2e")

        # main axes (left 75% of figure)
        self.ax_t = self.fig.add_axes([0.07, 0.12, 0.60, 0.78])
        self.ax_h = self.ax_t.twinx()

        self.ax_t.set_facecolor("#12121e")
        for spine in self.ax_t.spines.values():
            spine.set_edgecolor("#444")
        self.ax_t.tick_params(colors="#ccc")
        self.ax_h.tick_params(colors="#ccc")
        self.ax_t.yaxis.label.set_color("#ccc")
        self.ax_h.yaxis.label.set_color("#ccc")

        self.ax_t.set_ylabel("Temperature (°C)", color="#ccc")
        self.ax_h.set_ylabel("Relative Humidity (%)", color="#ccc")
        self.ax_t.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        self.ax_t.xaxis.set_major_locator(mdates.AutoDateLocator())
        self.fig.autofmt_xdate(rotation=30, ha="right")

        # ── lines ─────────────────────────────────────────────────────────────
        (self.ln_ch1_t,) = self.ax_t.plot([], [], color=COLOR_CH1_T, lw=1.3, label="CH1 Temp")
        (self.ln_ch3_t,) = self.ax_t.plot([], [], color=COLOR_CH3_T, lw=1.3, label="CH3 Temp", ls="--")
        (self.ln_ch1_h,) = self.ax_h.plot([], [], color=COLOR_CH1_H, lw=1.3, label="CH1 Hum")
        (self.ln_ch3_h,) = self.ax_h.plot([], [], color=COLOR_CH3_H, lw=1.3, label="CH3 Hum", ls="--")
        self.lines = {
            "CH1 Temp": self.ln_ch1_t,
            "CH3 Temp": self.ln_ch3_t,
            "CH1 Hum":  self.ln_ch1_h,
            "CH3 Hum":  self.ln_ch3_h,
        }

        # actuator spans (filled collections, rebuilt on each file load)
        self.span_collections = []

        # legend patches
        legend_patches = [
            Patch(facecolor=COLOR_CH1_T,  label="CH1 Temp"),
            Patch(facecolor=COLOR_CH3_T,  label="CH3 Temp"),
            Patch(facecolor=COLOR_CH1_H,  label="CH1 Hum"),
            Patch(facecolor=COLOR_CH3_H,  label="CH3 Hum"),
            Patch(facecolor=COLOR_HEATER, label="Heater ON", alpha=0.5),
            Patch(facecolor=COLOR_DRIER,  label="Drier ON",  alpha=0.5),
            Patch(facecolor=COLOR_HUMID,  label="Humidifier ON", alpha=0.5),
        ]
        self.ax_t.legend(handles=legend_patches, loc="upper left",
                         facecolor="#2a2a3e", edgecolor="#555", labelcolor="#ddd",
                         fontsize=8)

        self.title = self.fig.suptitle("", color="#eee", fontsize=11, fontweight="bold")

        # ── radio buttons (file select) ────────────────────────────────────────
        ax_radio = self.fig.add_axes([0.70, 0.55, 0.28, 0.38],
                                     facecolor="#2a2a3e")
        ax_radio.set_title("Session", color="#ccc", fontsize=9, pad=4)
        self.radio = RadioButtons(ax_radio, self.labels, activecolor=COLOR_CH1_T)
        for lbl in self.radio.labels:
            lbl.set_color("#ccc")
            lbl.set_fontsize(8)
        self.radio.on_clicked(self._on_radio)

        # ── checkboxes (series toggle) ─────────────────────────────────────────
        series_names = list(self.lines.keys())
        ax_chk = self.fig.add_axes([0.70, 0.10, 0.28, 0.38],
                                   facecolor="#2a2a3e")
        ax_chk.set_title("Series", color="#ccc", fontsize=9, pad=4)
        self.chk = CheckButtons(ax_chk, series_names,
                                actives=[True] * len(series_names))
        for lbl in self.chk.labels:
            lbl.set_color("#ccc")
            lbl.set_fontsize(8)
        self.chk.on_clicked(self._on_check)

        self._load(0)

    def _get_data(self, idx):
        if idx not in self.cache:
            self.cache[idx] = load_csv(self.files[idx])
        return self.cache[idx]

    def _clear_spans(self):
        for coll in self.span_collections:
            coll.remove()
        self.span_collections.clear()

    def _draw_spans(self, data):
        pairs = [
            ("heater",     COLOR_HEATER, 0.25),
            ("drier",      COLOR_DRIER,  0.25),
            ("humidifier", COLOR_HUMID,  0.25),
        ]
        for key, color, alpha in pairs:
            for start, end in _spans(data["ts"], data[key]):
                coll = self.ax_t.axvspan(start, end,
                                         facecolor=color, alpha=alpha, zorder=0)
                self.span_collections.append(coll)

    def _load(self, idx):
        data = self._get_data(idx)
        self._clear_spans()

        ts = data["ts"]
        self.ln_ch1_t.set_data(ts, data["ch1_t"])
        self.ln_ch3_t.set_data(ts, data["ch3_t"])
        self.ln_ch1_h.set_data(ts, data["ch1_h"])
        self.ln_ch3_h.set_data(ts, data["ch3_h"])

        self._draw_spans(data)

        self.ax_t.relim(); self.ax_t.autoscale_view()
        self.ax_h.relim(); self.ax_h.autoscale_view()

        n = len(ts)
        duration = (ts[-1] - ts[0]).total_seconds() / 60 if n > 1 else 0
        self.title.set_text(
            f"Session: {self.labels[idx]}  —  {n:,} samples  ({duration:.0f} min)"
        )
        self.fig.canvas.draw_idle()

    def _on_radio(self, label):
        idx = self.labels.index(label)
        self.current = idx
        self._load(idx)

    def _on_check(self, label):
        line = self.lines[label]
        line.set_visible(not line.get_visible())
        self.fig.canvas.draw_idle()

    def show(self):
        plt.show()


def main():
    pattern = os.path.join(DATA_DIR, "accel_reactor_*.csv")
    files = sorted(glob.glob(pattern))
    if not files:
        print(f"No CSV files found in {DATA_DIR}")
        return
    print(f"Found {len(files)} session(s):")
    for f in files:
        print(f"  {os.path.basename(f)}")
    Viewer(files).show()


if __name__ == "__main__":
    main()
