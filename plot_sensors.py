"""
Real-time serial plotter for XploraVentures.

On startup, reads the first DATA line to detect which SHT45 channels are
present, then builds a grid with only those subplots (plus VCELL).

Serial line format:
  DATA,<ts_s>,<vcell_V>,<t0>,<rh0>,<t1>,<rh1>,...,<t7>,<rh7>

Usage:
  pip install pyserial matplotlib
  python plot_sensors.py          # auto-detect port
  python plot_sensors.py COM3     # explicit port
"""

import math
import threading
import collections
import argparse
import serial
import serial.tools.list_ports
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
from matplotlib.animation import FuncAnimation

BAUD       = 115200
MAX_POINTS = 1200  # ~20 min at 1 s/sample
COLOR_T    = "tab:red"
COLOR_RH   = "tab:blue"
COLOR_V    = "tab:green"


# ── port detection ────────────────────────────────────────────────────────────

def find_port() -> str:
    for p in serial.tools.list_ports.comports():
        desc = (p.description or "").lower()
        if any(kw in desc for kw in ("cp210", "ch340", "ftdi", "usb serial", "uart")):
            return p.device
    candidates = serial.tools.list_ports.comports()
    if candidates:
        return candidates[0].device
    raise RuntimeError("No serial port found — pass the port as an argument.")


# ── DATA line parser ──────────────────────────────────────────────────────────

def parse_data_line(line: str):
    """Return (ts, vcell, temps[8], rhs[8]) or None if not a valid DATA line."""
    line = line.strip()
    if not line.startswith("DATA,"):
        return None
    parts = line.split(",")
    if len(parts) != 19:
        return None
    try:
        ts    = float(parts[1])
        vcell = float(parts[2])
        temps = [float(parts[3 + ch * 2])     for ch in range(8)]
        rhs   = [float(parts[3 + ch * 2 + 1]) for ch in range(8)]
    except ValueError:
        return None
    return ts, vcell, temps, rhs


# ── startup: detect active channels ──────────────────────────────────────────

def detect_active_channels(ser: serial.Serial) -> list[int]:
    """Block until the first DATA line arrives; return list of active channel indices."""
    print("Waiting for first DATA line to detect active sensors...")
    while True:
        raw  = ser.readline().decode("utf-8", errors="replace")
        parsed = parse_data_line(raw)
        if parsed is None:
            continue
        _, _, temps, _ = parsed
        active = [ch for ch in range(8) if not math.isnan(temps[ch])]
        print(f"  Active SHT45 channels: {active if active else 'none'}")
        return active


# ── shared data store ─────────────────────────────────────────────────────────

class SensorData:
    def __init__(self):
        self.lock  = threading.Lock()
        self.ts    = collections.deque(maxlen=MAX_POINTS)
        self.vcell = collections.deque(maxlen=MAX_POINTS)
        self.temp  = [collections.deque(maxlen=MAX_POINTS) for _ in range(8)]
        self.rh    = [collections.deque(maxlen=MAX_POINTS) for _ in range(8)]

    def ingest(self, line: str):
        parsed = parse_data_line(line)
        if parsed is None:
            return
        ts, vcell, temps, rhs = parsed
        with self.lock:
            self.ts.append(ts)
            self.vcell.append(None if math.isnan(vcell) else vcell)
            for ch in range(8):
                self.temp[ch].append(None if math.isnan(temps[ch]) else temps[ch])
                self.rh[ch].append(None  if math.isnan(rhs[ch])   else rhs[ch])


# ── serial reader thread ──────────────────────────────────────────────────────

def serial_reader(ser: serial.Serial, store: SensorData):
    while True:
        try:
            line = ser.readline().decode("utf-8", errors="replace")
            store.ingest(line)
        except serial.SerialException:
            break


# ── helpers ───────────────────────────────────────────────────────────────────

def _valid_pairs(xs, ys):
    px, py = [], []
    for x, y in zip(xs, ys):
        if x is not None and y is not None:
            px.append(x)
            py.append(y)
    return px, py


def _grid_shape(n: int):
    """Compact rows×cols grid that fits n subplots."""
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)
    return rows, cols


# ── build figure ──────────────────────────────────────────────────────────────

def build_figure(active_channels: list[int]):
    n_plots = 1 + len(active_channels)  # VCELL + one per active sensor
    rows, cols = _grid_shape(n_plots)

    fig = plt.figure(figsize=(5 * cols, 3.5 * rows))
    title = (f"XploraVentures — Live Sensor Monitor  "
             f"({'no temp sensors' if not active_channels else f'SHT45 CH: {active_channels}'})")
    fig.suptitle(title, fontsize=12, fontweight="bold")
    gs = gridspec.GridSpec(rows, cols, figure=fig, hspace=0.6, wspace=0.4)

    plot_positions = [(r, c) for r in range(rows) for c in range(cols)]

    # ── VCELL subplot ─────────────────────────────────────────────────────────
    ax_v = fig.add_subplot(gs[plot_positions[0]])
    ax_v.set_title("Battery (VCELL)")
    ax_v.set_ylabel("Voltage (V)")
    ax_v.set_ylim(2.8, 4.4)
    ln_v, = ax_v.plot([], [], color=COLOR_V, linewidth=1.5)
    ax_v.axhline(3.0,  color="red",    linestyle="--", linewidth=0.8, alpha=0.6)
    ax_v.axhline(4.25, color="orange", linestyle="--", linewidth=0.8, alpha=0.6)
    ax_v.set_xlabel("Time (s)")

    # ── SHT45 subplots ────────────────────────────────────────────────────────
    ch_axes_t  = {}   # ch → primary ax (temp)
    ch_axes_rh = {}   # ch → twin ax (rh)
    ch_ln_t    = {}
    ch_ln_rh   = {}

    for idx, ch in enumerate(active_channels):
        ax = fig.add_subplot(gs[plot_positions[1 + idx]])
        ax.set_title(f"SHT45 CH{ch}")
        ax.set_ylabel("Temp (°C)", color=COLOR_T)
        ax.tick_params(axis="y", labelcolor=COLOR_T)
        ax.set_xlabel("Time (s)")

        ax2 = ax.twinx()
        ax2.set_ylabel("%RH", color=COLOR_RH)
        ax2.tick_params(axis="y", labelcolor=COLOR_RH)
        ax2.set_ylim(0, 100)

        lt, = ax.plot([], [],  color=COLOR_T,  linewidth=1.2)
        lr, = ax2.plot([], [], color=COLOR_RH, linewidth=1.2, linestyle="--")

        ch_axes_t[ch]  = ax
        ch_axes_rh[ch] = ax2
        ch_ln_t[ch]    = lt
        ch_ln_rh[ch]   = lr

    # hide any unused grid cells
    for pos in plot_positions[n_plots:]:
        fig.add_subplot(gs[pos]).set_visible(False)

    return fig, ax_v, ln_v, ch_axes_t, ch_ln_t, ch_ln_rh


# ── entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="XploraVentures live sensor plotter")
    parser.add_argument("port", nargs="?", help="Serial port (e.g. COM3 or /dev/ttyUSB0)")
    args = parser.parse_args()

    port = args.port or find_port()
    print(f"Opening {port} at {BAUD} baud...")

    ser = serial.Serial(port, BAUD, timeout=5)
    active_channels = detect_active_channels(ser)

    store = SensorData()

    # replay the lines already buffered in the detect step isn't possible, but
    # the reader thread will pick up from here
    t = threading.Thread(target=serial_reader, args=(ser, store), daemon=True)
    t.start()

    fig, ax_v, ln_v, ch_axes_t, ch_ln_t, ch_ln_rh = build_figure(active_channels)

    def update(_):
        with store.lock:
            ts    = list(store.ts)
            vcell = list(store.vcell)
            temps = {ch: list(store.temp[ch]) for ch in active_channels}
            rhs   = {ch: list(store.rh[ch])   for ch in active_channels}

        if not ts:
            return

        xv, yv = _valid_pairs(ts, vcell)
        if xv:
            ln_v.set_data(xv, yv)
            ax_v.set_xlim(xv[0], max(xv[-1], xv[0] + 60))
            ax_v.relim()
            ax_v.autoscale_view(scaley=False)

        for ch in active_channels:
            xt, yt = _valid_pairs(ts, temps[ch])
            xr, yr = _valid_pairs(ts, rhs[ch])
            if xt:
                ch_ln_t[ch].set_data(xt, yt)
                ch_axes_t[ch].set_xlim(xt[0], max(xt[-1], xt[0] + 60))
                ch_axes_t[ch].relim()
                ch_axes_t[ch].autoscale_view(scaley=True)
            if xr:
                ch_ln_rh[ch].set_data(xr, yr)

    fig.ani = FuncAnimation(fig, update, interval=1000, cache_frame_data=False)
    plt.show()


if __name__ == "__main__":
    main()
