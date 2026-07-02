#!/usr/bin/env python3
"""
Zeolite wet mass estimator.

Reconstructs absolute water content from the cycle-relative water_absorbed_g /
water_released_g integrals in the reactor CSV.

If those columns are all zero (flow_discharge / flow_charge were not configured
in the dashboard), the script recomputes them from raw flow_slpm × ΔAH.

Usage:
    python analysis/wet_mass.py DATA/accel_reactor_20260619_141548.csv 50.0
    python analysis/wet_mass.py DATA/accel_reactor_20260619_141548.csv 50.0 --initial_water 2.1
"""

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches

REGEN_PHASES  = {"charging", "regenerating"}
ABSORB_PHASES = {"discharging"}

PHASE_COLORS = {
    "discharging":  "#2196F3",
    "charging":     "#FF5722",
    "regenerating": "#FF5722",
    "cooling":      "#4CAF50",
    "paused":       "#9E9E9E",
    "stopped":      "#9E9E9E",
}


# ── Fallback integral computation ────────────────────────────────────────────

def recompute_integrals(df):
    """
    Recompute cycle-relative water_absorbed_g / water_released_g from raw
    flow_slpm × ΔAH when the logged columns are all zero.

    Mirrors cycle_runner.py exactly:
        flux [g/min] = flow_slpm × AH_diff / 1000
        integral    += flux × Δt [min]
    """
    dt_min = (pd.to_datetime(df["timestamp"]).diff()
                .dt.total_seconds().fillna(2.0) / 60.0).values

    phases  = df["phase"].values
    flow    = df["flow_slpm"].values
    dah_abs = np.maximum(0.0, df["ch1_ah"].values - df["ch3_ah"].values)
    dah_rel = np.maximum(0.0, df["ch3_ah"].values - df["ch1_ah"].values)

    absorbed = np.zeros(len(df))
    released = np.zeros(len(df))
    acc_abs = acc_rel = 0.0
    prev_phase = None

    for i in range(len(df)):
        phase = phases[i]
        if phase != prev_phase:
            acc_abs = acc_rel = 0.0

        if phase in ABSORB_PHASES:
            acc_abs += flow[i] * dah_abs[i] / 1000.0 * dt_min[i]
            absorbed[i] = acc_abs
        elif phase in REGEN_PHASES:
            acc_rel += flow[i] * dah_rel[i] / 1000.0 * dt_min[i]
            released[i] = acc_rel

        prev_phase = phase

    return absorbed, released


# ── Core reconstruction ───────────────────────────────────────────────────────

def reconstruct_water_content(phases, absorbed, released, initial_water_g=0.0):
    """
    Return absolute water content [g] at every timestep.

    water_absorbed_g and water_released_g both reset to 0 at the start of each
    new phase. We carry a running baseline across transitions so the result is
    continuous and absolute (not cycle-relative).
    """
    n = len(phases)
    water = np.empty(n)
    current = float(initial_water_g)
    baseline = float(initial_water_g)
    prev_phase = None

    for i in range(n):
        phase = phases[i]

        if phase != prev_phase:
            baseline = current          # snapshot at every phase transition

        if phase in ABSORB_PHASES:
            current = baseline + absorbed[i]
        elif phase in REGEN_PHASES:
            current = max(0.0, baseline - released[i])
        # cooling / paused / stopped: hold last value

        water[i] = current
        prev_phase = phase

    return water


# ── Per-cycle summary ─────────────────────────────────────────────────────────

def cycle_summary(df, dry_mass_g):
    rows = []
    cycle_id = (df["phase"] != df["phase"].shift()).cumsum()
    cycle_num = 0

    for _, grp in df.groupby(cycle_id, sort=False):
        phase = grp["phase"].iloc[0]
        if phase not in ABSORB_PHASES:
            continue
        cycle_num += 1
        peak_wc      = grp["water_content_g"].max()
        start_wc     = grp["water_content_g"].iloc[0]
        uptake       = peak_wc - start_wc
        t_start      = pd.to_datetime(grp["timestamp"].iloc[0])
        t_end        = pd.to_datetime(grp["timestamp"].iloc[-1])
        duration_min = (t_end - t_start).total_seconds() / 60
        rows.append({
            "cycle":            cycle_num,
            "start":            grp["timestamp"].iloc[0],
            "duration_min":     round(duration_min, 1),
            "start_wet_mass_g": round(dry_mass_g + start_wc, 3),
            "peak_wet_mass_g":  round(dry_mass_g + peak_wc, 3),
            "water_uptake_g":   round(uptake, 3),
            "uptake_pct_dm":    round(uptake / dry_mass_g * 100, 2),
        })
    return pd.DataFrame(rows)


# ── Plotting ──────────────────────────────────────────────────────────────────

def shade_phases(ax, ts, phases):
    cycle_id = (phases != phases.shift()).cumsum()
    for _, grp in phases.groupby(cycle_id, sort=False):
        phase  = grp.iloc[0]
        color  = PHASE_COLORS.get(phase, "#9E9E9E")
        t0, t1 = ts.iloc[grp.index[0]], ts.iloc[grp.index[-1]]
        ax.axvspan(t0, t1, alpha=0.07, color=color, linewidth=0)


def make_plot(df, dry_mass_g, out_path):
    ts = pd.to_datetime(df["timestamp"])

    fig, axes = plt.subplots(3, 1, figsize=(15, 10), sharex=True)
    fig.patch.set_facecolor("#1a1a2e")
    for ax in axes:
        ax.set_facecolor("#16213e")
        ax.tick_params(colors="white")
        ax.spines[:].set_color("#444")
        shade_phases(ax, ts, df["phase"])

    # ── Panel 1: wet mass ─────────────────────────────────────────────────────
    ax = axes[0]
    ax.plot(ts, df["wet_mass_g"], color="#2196F3", linewidth=1.5, label="Estimated wet mass")
    ax.axhline(dry_mass_g, color="white", linewidth=0.8, linestyle="--",
               alpha=0.5, label=f"Dry mass ({dry_mass_g:.2f} g)")
    ax.set_ylabel("Mass (g)", color="white")
    ax.set_title("Zeolite Wet Mass", color="white", fontsize=11)
    ax.legend(fontsize=8, facecolor="#16213e", labelcolor="white")
    ax.grid(True, alpha=0.15)

    # ── Panel 2: water content ────────────────────────────────────────────────
    ax = axes[1]
    ax.plot(ts, df["water_content_g"], color="#4CAF50", linewidth=1.5)
    ax.fill_between(ts, 0, df["water_content_g"], alpha=0.2, color="#4CAF50")
    ax.set_ylabel("Water in zeolite (g)", color="white")
    ax.set_title("Absolute Water Content", color="white", fontsize=11)
    ax.grid(True, alpha=0.15)

    # ── Panel 3: ΔAH (sensor signal) and bed temp ────────────────────────────
    ax  = axes[2]
    ax2 = ax.twinx()
    ax2.set_facecolor("#16213e")
    ax2.tick_params(colors="white")
    ax2.spines[:].set_color("#444")

    dah = df["ch1_ah"] - df["ch3_ah"]
    ax.plot(ts, dah, color="#FF9800", linewidth=0.9, label="ΔAH inlet−outlet (g/m³)")
    ax.axhline(0, color="white", linewidth=0.4, alpha=0.3)
    ax.set_ylabel("ΔAH (g/m³)", color="#FF9800")
    ax.tick_params(axis="y", labelcolor="#FF9800")

    ax2.plot(ts, df["rtd_t"], color="#F44336", linewidth=0.8, alpha=0.7, label="RTD temp (°C)")
    ax2.set_ylabel("Bed temp (°C)", color="#F44336")
    ax2.tick_params(axis="y", labelcolor="#F44336")

    ax.set_xlabel("Time", color="white")
    ax.set_title("ΔAH Signal & Bed Temperature", color="white", fontsize=11)
    ax.grid(True, alpha=0.15)

    lines  = ax.get_lines()  + ax2.get_lines()
    labels = [l.get_label() for l in lines]
    ax.legend(lines, labels, fontsize=8, facecolor="#16213e", labelcolor="white")

    # Phase legend
    patches = [mpatches.Patch(color=PHASE_COLORS[p], label=p, alpha=0.6)
               for p in ("discharging", "charging", "cooling")]
    axes[0].legend(handles=patches + axes[0].get_lines(),
                   fontsize=7, facecolor="#16213e", labelcolor="white")

    fig.autofmt_xdate()
    plt.tight_layout(rect=[0, 0, 1, 0.97])
    plt.savefig(out_path, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    print(f"  Plot:   {out_path}")
    plt.close()


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Estimate zeolite wet mass from reactor CSV data."
    )
    parser.add_argument("csv",          help="Path to reactor CSV file")
    parser.add_argument("dry_weight_g", type=float, help="Measured dry zeolite mass (g) from scale")
    parser.add_argument("wet_weight_g", type=float, help="Measured wet zeolite mass (g) from scale, at peak saturation before regen")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        sys.exit(f"File not found: {csv_path}")

    dry_weight_g    = args.dry_weight_g
    wet_weight_g    = args.wet_weight_g
    measured_uptake = wet_weight_g - dry_weight_g

    print(f"\nFile:            {csv_path.name}")
    print(f"Measured dry:    {dry_weight_g:.3f} g")
    print(f"Measured wet:    {wet_weight_g:.3f} g")
    print(f"Measured uptake: {measured_uptake:.3f} g\n")

    df = pd.read_csv(csv_path)

    required = ["timestamp", "phase", "water_absorbed_g", "water_released_g", "ch1_ah", "ch3_ah", "rtd_t"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        sys.exit(f"Missing columns in CSV: {missing}")

    for col in ("water_absorbed_g", "water_released_g", "ch1_ah", "ch3_ah", "rtd_t"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    # If logged integrals are zero but flow_slpm is present, recompute from raw data.
    # This happens when flow_discharge / flow_charge were not configured in the dashboard.
    integrals_missing = (df["water_absorbed_g"].max() == 0.0 and
                         df["water_released_g"].max() == 0.0)
    has_flow = "flow_slpm" in df.columns and pd.to_numeric(df["flow_slpm"], errors="coerce").max() > 0

    if integrals_missing and has_flow:
        print("NOTE: water_absorbed_g / water_released_g are all zero — recomputing from flow_slpm × ΔAH.\n"
              "      (flow_discharge / flow_charge were not set in the dashboard.)\n")
        df["flow_slpm"] = pd.to_numeric(df["flow_slpm"], errors="coerce").fillna(0.0)
        df["water_absorbed_g"], df["water_released_g"] = recompute_integrals(df)
    elif integrals_missing:
        print("WARNING: water_absorbed_g is all zero and flow_slpm is missing or zero.\n"
              "         Cannot compute water content — check flow sensor.\n")

    df["water_content_g"] = reconstruct_water_content(
        df["phase"].values,
        df["water_absorbed_g"].values,
        df["water_released_g"].values,
        0.0,
    )
    df["wet_mass_g"] = dry_weight_g + df["water_content_g"]

    # ── Inferred values ───────────────────────────────────────────────────────
    # From discharge: peak absorbed = inferred uptake → inferred wet mass
    peak_water    = df.loc[df["phase"].isin(ABSORB_PHASES), "water_content_g"].max()
    inferred_wet  = dry_weight_g + peak_water

    # From regen: water released → inferred dry mass
    cycle_id = (df["phase"] != df["phase"].shift()).cumsum()
    water_released_in_regen = 0.0
    residual_after_regen    = None
    for _, grp in df.groupby(cycle_id, sort=False):
        if grp["phase"].iloc[0] in REGEN_PHASES:
            released = grp["water_released_g"].iloc[-1]
            if released > 0:
                water_released_in_regen = released
                residual_after_regen    = grp["water_content_g"].iloc[-1]
                break
    inferred_dry = wet_weight_g - water_released_in_regen

    # ── Comparison ────────────────────────────────────────────────────────────
    W = 66
    print("─" * W)
    print(f"{'':25s} {'Measured':>9s}  {'Discharge':>9s}  {'Regen':>9s}")
    print("─" * W)

    def _diff(inferred, measured):
        if inferred is None:
            return f"{'—':>9s}  {'':>7s}"
        return f"{inferred:9.3f}  {inferred - measured:+7.3f}"

    # Dry mass: inferred from regen only
    dry_regen = f"{inferred_dry:9.3f}  {inferred_dry - dry_weight_g:+7.3f}" if water_released_in_regen > 0 else f"{'—':>9s}  {'':>7s}"
    print(f"{'Dry mass (g)':25s} {dry_weight_g:9.3f}  {'—':>9s}  {'':>7s}  {dry_regen}")

    # Wet mass: inferred from discharge only
    print(f"{'Wet mass (g)':25s} {wet_weight_g:9.3f}  {inferred_wet:9.3f}  {inferred_wet - wet_weight_g:+7.3f}  {'—':>9s}  {'':>7s}")

    # Uptake: both discharge and regen give independent estimates
    regen_uptake = water_released_in_regen if water_released_in_regen > 0 else None
    print(f"{'Water uptake (g)':25s} {measured_uptake:9.3f}  {peak_water:9.3f}  {peak_water - measured_uptake:+7.3f}  "
          + (_diff(regen_uptake, measured_uptake)))
    print("─" * W)
    print(f"{'':25s} {'':9s}  {'(diff vs meas)':>20s}  {'(diff vs meas)':>20s}")

    if water_released_in_regen == 0:
        print("\nWARNING: No regen data found — regen column unavailable.")
    elif residual_after_regen is not None and residual_after_regen > 0.05:
        print(f"\nNOTE: ~{residual_after_regen:.3f} g residual water after regen — "
              f"regen may be incomplete, inferred dry mass slightly low.")

    # ── Per-cycle summary ─────────────────────────────────────────────────────
    summary = cycle_summary(df, dry_weight_g)
    if not summary.empty:
        print("\nDischarge cycle summary:")
        print(summary.to_string(index=False))

    # ── Outputs ───────────────────────────────────────────────────────────────
    out_dir = Path(__file__).parent / "output"
    out_dir.mkdir(exist_ok=True)
    stem = csv_path.stem

    out_csv = out_dir / f"{stem}_wet_mass.csv"
    df[["timestamp", "phase", "water_content_g", "wet_mass_g",
        "water_absorbed_g", "water_released_g",
        "ch1_ah", "ch3_ah", "rtd_t"]].to_csv(out_csv, index=False)
    print(f"\n  CSV:    {out_csv}")

    make_plot(df, dry_weight_g, out_dir / f"{stem}_wet_mass.png")

    if not summary.empty:
        summary.to_csv(out_dir / f"{stem}_cycle_summary.csv", index=False)
        print(f"  Stats:  {out_dir / f'{stem}_cycle_summary.csv'}")


if __name__ == "__main__":
    main()
