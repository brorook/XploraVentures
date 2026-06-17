"""
Offline viewer for accelerated-reactor CSV logs in DATA/.

Controls
--------
  Radio buttons  : switch between session files
  Checkboxes     : toggle individual series
  Q dis / Q chg  : volumetric flow rates during discharge / charge phases (L/min)
  Dry wt         : dry mass of sorbent (g) — used to normalise water uptake
  Mouse scroll   : zoom x-axis (matplotlib built-in)

Water uptake (lower subplot)
----------------------------
  Absolute humidity difference (CH1 inlet − CH3 outlet) integrated over time
  and scaled by the phase flow rate.  Positive = material absorbing, negative
  = material desorbing.  Y-axis shows g/g_dry when dry weight > 0.
"""

import os
import glob
import csv
import datetime
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.lines import Line2D
from matplotlib.transforms import blended_transform_factory
from matplotlib.widgets import RadioButtons, CheckButtons, TextBox
from matplotlib.patches import Patch
import numpy as np

DATA_DIR = os.path.join(os.path.dirname(__file__), "DATA")

COLOR_CH1_T  = "#e05252"
COLOR_CH3_T  = "#e08c52"
COLOR_CH1_H  = "#5285e0"
COLOR_CH3_H  = "#52c4e0"
COLOR_HEATER = "#ffaaaa"
COLOR_DRIER  = "#aaaaff"   # blue/purple for drier
COLOR_HUMID  = "#aaffaa"   # green for humidifier
COLOR_WATER  = "#c8a0f0"


def absolute_humidity(T_C, RH_pct):
    """Return absolute humidity in g/m³ (Tetens formula).
    P_v [Pa] × M [g/mol] / (R [Pa·m³/mol/K] × T [K])  →  g/m³  (no extra factor needed)
    """
    P_sat = 610.78 * np.exp(17.27 * T_C / (T_C + 237.3))
    P_v   = (RH_pct / 100.0) * P_sat
    return P_v * 18.015 / (8.314 * (T_C + 273.15))


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


def _calc_water_uptake(data, q_dis_lpm, q_chg_lpm):
    """
    Cumulative water absorbed by sorbent [g].
    CH1 = inlet, CH3 = outlet.
    Positive = absorbed (humidifier phase), negative = desorbed (heater/drier phase).
    """
    ts  = data["ts"]
    n   = len(ts)
    if n < 2:
        return np.zeros(n)

    dt = np.array([(ts[i + 1] - ts[i]).total_seconds() for i in range(n - 1)],
                  dtype=float)

    humidifier = np.array(data["humidifier"][:-1], dtype=float)
    drier      = np.array(data["drier"][:-1],      dtype=float)

    # m³/s from L/min
    q = np.where(humidifier > 0, q_dis_lpm / 60000.0,
        np.where(drier > 0, q_chg_lpm / 60000.0, 0.0))

    T1 = np.array(data["ch1_t"][:-1], dtype=float)
    H1 = np.array(data["ch1_h"][:-1], dtype=float)
    T3 = np.array(data["ch3_t"][:-1], dtype=float)
    H3 = np.array(data["ch3_h"][:-1], dtype=float)

    AH_in  = absolute_humidity(T1, H1)   # g/m³
    AH_out = absolute_humidity(T3, H3)

    dW = q * (AH_in - AH_out) * dt       # g per interval

    uptake      = np.zeros(n)
    uptake[1:]  = np.cumsum(dW)
    return uptake


def label_from_path(path):
    name = os.path.basename(path)
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
        self.files  = files
        self.labels = [label_from_path(f) for f in files]
        self.cache  = {}
        self.current = 0

        # default flow / mass inputs
        self.q_dis     = 10.0  # L/min  (discharge / humidification phase)
        self.q_chg     = 10.0  # L/min  (charge / drying phase)
        self.m_dry     = 1.0   # g      (dry sorbent mass)
        self.m_wet     = 0.0   # g      (mass after humidification)
        self.m_post_dry = 0.0  # g      (mass after drying)

        # ── figure ────────────────────────────────────────────────────────────
        self.fig = plt.figure(figsize=(14, 7))
        self.fig.patch.set_facecolor("#1e1e2e")

        # main temp/hum axes  (left 60%, upper portion)
        self.ax_t = self.fig.add_axes([0.07, 0.27, 0.60, 0.63])
        self.ax_h = self.ax_t.twinx()

        for ax in (self.ax_t, self.ax_h):
            ax.tick_params(colors="#ccc")
        self.ax_t.set_facecolor("#12121e")
        for spine in self.ax_t.spines.values():
            spine.set_edgecolor("#444")
        self.ax_t.set_ylabel("Temperature (°C)", color="#ccc")
        self.ax_h.set_ylabel("Relative Humidity (%)", color="#ccc")
        self.ax_t.yaxis.label.set_color("#ccc")
        self.ax_h.yaxis.label.set_color("#ccc")
        self.ax_t.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        self.ax_t.xaxis.set_major_locator(mdates.AutoDateLocator())
        plt.setp(self.ax_t.get_xticklabels(), visible=False)

        # water uptake axes  (shares x with ax_t, sits below)
        self.ax_w = self.fig.add_axes([0.07, 0.09, 0.60, 0.14],
                                      sharex=self.ax_t)
        self.ax_w.set_facecolor("#12121e")
        for spine in self.ax_w.spines.values():
            spine.set_edgecolor("#444")
        self.ax_w.tick_params(colors="#ccc", labelsize=7)
        self.ax_w.set_ylabel("Water uptake\n(g/g dry)", color="#ccc", fontsize=7)
        self.ax_w.yaxis.label.set_color("#ccc")
        self._h_baseline = self.ax_w.axhline(0, color="#555", lw=0.8, ls="--", zorder=1)
        self.ax_w.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
        self.ax_w.xaxis.set_major_locator(mdates.AutoDateLocator())
        self.fig.autofmt_xdate(rotation=30, ha="right")

        # ── series lines ──────────────────────────────────────────────────────
        (self.ln_ch1_t,) = self.ax_t.plot([], [], color=COLOR_CH1_T, lw=1.3, label="CH1 Temp")
        (self.ln_ch3_t,) = self.ax_t.plot([], [], color=COLOR_CH3_T, lw=1.3, label="CH3 Temp", ls="--")
        (self.ln_ch1_h,) = self.ax_h.plot([], [], color=COLOR_CH1_H, lw=1.3, label="CH1 Hum")
        (self.ln_ch3_h,) = self.ax_h.plot([], [], color=COLOR_CH3_H, lw=1.3, label="CH3 Hum", ls="--")
        (self.ln_water,) = self.ax_w.plot([], [], color=COLOR_WATER,  lw=1.3, label="Water uptake")

        self.lines = {
            "CH1 Temp": self.ln_ch1_t,
            "CH3 Temp": self.ln_ch3_t,
            "CH1 Hum":  self.ln_ch1_h,
            "CH3 Hum":  self.ln_ch3_h,
        }

        self.span_collections = []

        # legend
        legend_patches = [
            Patch(facecolor=COLOR_CH1_T,  label="CH1 Temp"),
            Patch(facecolor=COLOR_CH3_T,  label="CH3 Temp"),
            Patch(facecolor=COLOR_CH1_H,  label="CH1 Hum"),
            Patch(facecolor=COLOR_CH3_H,  label="CH3 Hum"),
            Patch(facecolor=COLOR_HEATER, label="Heater ON",     alpha=0.5),
            Patch(facecolor=COLOR_HUMID,  label="Humidifier ON", alpha=0.5),
            Patch(facecolor=COLOR_DRIER,  label="Drier ON",      alpha=0.5),
        ]
        self.ax_t.legend(handles=legend_patches, loc="upper left",
                         facecolor="#2a2a3e", edgecolor="#555", labelcolor="#ddd",
                         fontsize=8)

        self.title = self.fig.suptitle("", color="#eee", fontsize=11, fontweight="bold")

        # ── right panel: session radio ─────────────────────────────────────────
        ax_radio = self.fig.add_axes([0.70, 0.55, 0.28, 0.38], facecolor="#2a2a3e")
        ax_radio.set_title("Session", color="#ccc", fontsize=9, pad=4)
        self.radio = RadioButtons(ax_radio, self.labels, activecolor=COLOR_CH1_T)
        for lbl in self.radio.labels:
            lbl.set_color("#ccc")
            lbl.set_fontsize(8)
        self.radio.on_clicked(self._on_radio)

        # ── right panel: series checkboxes ────────────────────────────────────
        series_names = list(self.lines.keys())
        ax_chk = self.fig.add_axes([0.70, 0.10, 0.28, 0.38], facecolor="#2a2a3e")
        ax_chk.set_title("Series", color="#ccc", fontsize=9, pad=4)
        self.chk = CheckButtons(ax_chk, series_names,
                                actives=[True] * len(series_names))
        for lbl in self.chk.labels:
            lbl.set_color("#ccc")
            lbl.set_fontsize(8)
        self.chk.on_clicked(self._on_check)

        # ── bottom: flow / mass TextBoxes ─────────────────────────────────────
        tb_style = dict(color="#2a2a3e", hovercolor="#3a3a5e")

        ax_tb1 = self.fig.add_axes([0.14, 0.014, 0.09, 0.038])
        self.tb_q_dis = TextBox(ax_tb1, "Q dis (L/min) ", initial="10.0", **tb_style)
        self.tb_q_dis.on_submit(self._on_q_dis)

        ax_tb2 = self.fig.add_axes([0.33, 0.014, 0.09, 0.038])
        self.tb_q_chg = TextBox(ax_tb2, "Q chg (L/min) ", initial="10.0", **tb_style)
        self.tb_q_chg.on_submit(self._on_q_chg)

        ax_tb3 = self.fig.add_axes([0.51, 0.014, 0.09, 0.038])
        self.tb_m_dry = TextBox(ax_tb3, "Dry wt (g) ", initial="1.0", **tb_style)
        self.tb_m_dry.on_submit(self._on_m_dry)

        ax_tb4 = self.fig.add_axes([0.14, 0.058, 0.09, 0.038])
        self.tb_m_wet = TextBox(ax_tb4, "Wet wt (g) ", initial="0.0", **tb_style)
        self.tb_m_wet.on_submit(self._on_m_wet)

        ax_tb5 = self.fig.add_axes([0.33, 0.058, 0.09, 0.038])
        self.tb_m_post_dry = TextBox(ax_tb5, "Post-dry wt (g) ", initial="0.0", **tb_style)
        self.tb_m_post_dry.on_submit(self._on_m_post_dry)

        self._stats_text = self.fig.text(0.51, 0.077, "", color="#fbbf24", fontsize=8, va="center")

        # ── hover cursor ──────────────────────────────────────────────────────
        # Use raw Line2D so xdata starts at nan and never registers x=0 (epoch)
        # in relim/autoscale.
        for ax, attr in [(self.ax_t, "_vline_t"), (self.ax_w, "_vline_w")]:
            trans = blended_transform_factory(ax.transData, ax.transAxes)
            vl = Line2D([np.nan, np.nan], [0, 1], transform=trans,
                        color="#aaa", lw=0.7, ls=":", visible=False, zorder=5)
            ax.add_line(vl)
            setattr(self, attr, vl)
        self._tooltip = self.ax_t.text(
            0.02, 0.97, "", transform=self.ax_t.transAxes,
            va="top", ha="left", fontsize=7.5, color="#eee",
            bbox=dict(boxstyle="round,pad=0.4", facecolor="#1a1a2e",
                      edgecolor="#666", alpha=0.90),
            visible=False, zorder=10,
        )
        self._ts_num  = np.array([])
        self._y_water = np.array([])
        self.fig.canvas.mpl_connect("motion_notify_event", self._on_hover)
        self.fig.canvas.mpl_connect("axes_leave_event",    self._on_leave)

        self._load(0)

    # ── data helpers ──────────────────────────────────────────────────────────

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
            ("humidifier", COLOR_HUMID,  0.25),
            ("drier",      COLOR_DRIER,  0.25),
        ]
        for key, color, alpha in pairs:
            for start, end in _spans(data["ts"], data[key]):
                for ax in (self.ax_t, self.ax_w):
                    coll = ax.axvspan(start, end,
                                      facecolor=color, alpha=alpha, zorder=0)
                    self.span_collections.append(coll)

    def _update_water(self, data):
        uptake = _calc_water_uptake(data, self.q_dis, self.q_chg)
        y = self.m_dry + uptake          # absolute sample mass starting at dry weight
        self._y_water = y
        self.ln_water.set_data(data["ts"], y)
        self._h_baseline.set_ydata([self.m_dry, self.m_dry])
        self.ax_w.set_ylabel("Sample\nmass (g)", color="#ccc", fontsize=7)
        self.ax_w.relim()
        self.ax_w.autoscale_view(scalex=False)

    # ── load / update ─────────────────────────────────────────────────────────

    def _load(self, idx):
        data = self._get_data(idx)
        self._clear_spans()

        ts = data["ts"]
        self.ln_ch1_t.set_data(ts, data["ch1_t"])
        self.ln_ch3_t.set_data(ts, data["ch3_t"])
        self.ln_ch1_h.set_data(ts, data["ch1_h"])
        self.ln_ch3_h.set_data(ts, data["ch3_h"])

        self._draw_spans(data)
        self._update_water(data)
        self._ts_num = mdates.date2num(ts)

        if ts:
            self.ax_t.set_xlim(ts[0], ts[-1])
        self.ax_t.relim(); self.ax_t.autoscale_view()
        self.ax_h.relim(); self.ax_h.autoscale_view(scalex=False)

        n        = len(ts)
        duration = (ts[-1] - ts[0]).total_seconds() / 60 if n > 1 else 0
        self.title.set_text(
            f"Session: {self.labels[idx]}  —  {n:,} samples  ({duration:.0f} min)"
        )
        self.fig.canvas.draw_idle()

    # ── callbacks ─────────────────────────────────────────────────────────────

    def _on_radio(self, label):
        idx = self.labels.index(label)
        self.current = idx
        self._load(idx)

    def _on_check(self, label):
        self.lines[label].set_visible(not self.lines[label].get_visible())
        self.fig.canvas.draw_idle()

    def _on_q_dis(self, val):
        try:
            self.q_dis = float(val)
            self._update_water(self._get_data(self.current))
            self.fig.canvas.draw_idle()
        except ValueError:
            pass

    def _on_q_chg(self, val):
        try:
            self.q_chg = float(val)
            self._update_water(self._get_data(self.current))
            self.fig.canvas.draw_idle()
        except ValueError:
            pass

    def _on_m_dry(self, val):
        try:
            self.m_dry = float(val)
            self._update_water(self._get_data(self.current))
            self._update_gravimetric_stats()
            self.fig.canvas.draw_idle()
        except ValueError:
            pass

    def _on_m_wet(self, val):
        try:
            self.m_wet = float(val)
            self._update_gravimetric_stats()
        except ValueError:
            pass

    def _on_m_post_dry(self, val):
        try:
            self.m_post_dry = float(val)
            self._update_gravimetric_stats()
        except ValueError:
            pass

    def _update_gravimetric_stats(self):
        dry, wet, post = self.m_dry, self.m_wet, self.m_post_dry
        if dry <= 0 or wet <= dry:
            self._stats_text.set_text("")
            self.fig.canvas.draw_idle()
            return
        uptake = wet - dry
        parts  = [f"Uptake {uptake:.3f} g  ({uptake/dry*100:.1f}% w/w)"]
        if 0 < post < wet:
            released = wet - post
            parts.append(f"Released {released:.3f} g  ({released/dry*100:.1f}% w/w)")
            parts.append(f"Regen {released/uptake*100:.1f}%")
        self._stats_text.set_text("  |  ".join(parts))
        self.fig.canvas.draw_idle()

    def _on_hover(self, event):
        if event.inaxes not in (self.ax_t, self.ax_w) or self._ts_num.size == 0:
            self._hide_hover()
            return
        x = event.xdata
        if x is None:
            self._hide_hover()
            return

        idx  = int(np.clip(np.searchsorted(self._ts_num, x), 0, len(self._ts_num) - 1))
        data = self._get_data(self.current)

        state = " · ".join(
            lbl for lbl, key in [("HTR", "heater"), ("HUM", "humidifier"), ("DRY", "drier")]
            if data[key][idx]
        )
        mass = self._y_water[idx] if idx < len(self._y_water) else float("nan")

        tip = (
            f"{data['ts'][idx].strftime('%H:%M:%S')}  {state}\n"
            f"CH1  {data['ch1_t'][idx]:.1f} °C   {data['ch1_h'][idx]:.1f} %RH\n"
            f"CH3  {data['ch3_t'][idx]:.1f} °C   {data['ch3_h'][idx]:.1f} %RH\n"
            f"Mass {mass:.4f} g"
        )

        for vl in (self._vline_t, self._vline_w):
            vl.set_xdata([x, x])
            vl.set_visible(True)

        xlim    = self.ax_t.get_xlim()
        ax_frac = (x - xlim[0]) / (xlim[1] - xlim[0]) if xlim[1] != xlim[0] else 0.0
        tx, ha  = (0.02, "left") if ax_frac > 0.6 else (0.98, "right")
        self._tooltip.set_position((tx, 0.97))
        self._tooltip.set_ha(ha)
        self._tooltip.set_text(tip)
        self._tooltip.set_visible(True)

        self.fig.canvas.draw_idle()

    def _on_leave(self, _):
        self._hide_hover()

    def _hide_hover(self):
        changed = any(vl.get_visible() for vl in (self._vline_t, self._vline_w))
        changed = changed or self._tooltip.get_visible()
        for vl in (self._vline_t, self._vline_w):
            vl.set_visible(False)
        self._tooltip.set_visible(False)
        if changed:
            self.fig.canvas.draw_idle()

    def show(self):
        plt.show()


def main():
    pattern = os.path.join(DATA_DIR, "accel_reactor_*.csv")
    files   = sorted(glob.glob(pattern))
    if not files:
        print(f"No CSV files found in {DATA_DIR}")
        return
    print(f"Found {len(files)} session(s):")
    for f in files:
        print(f"  {os.path.basename(f)}")
    Viewer(files).show()


if __name__ == "__main__":
    main()
