#!/usr/bin/env python3
# pyright: reportMissingImports=false, reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
"""
Assignment 3: Field-Year Dashboard Prototype

Selects one field-year, aligns NDVI and weather records, detects agronomic
events, and produces a static 3-panel dashboard image.

Usage (prototype, single field-year):

    export DATA_PIPELINE_DATA_ROOT=$HOME/my-farm-advisor-runtime
    cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
    "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
      scripts/assignment3/assignment3_main.py \
      --grower-slug iowa-grower \
      --farm-slug iowa-grower-iowa \
      --field-id osm-1360386537 \
      --year 2024

Usage (all years for a field):

    ... --all-years

Output:
    shared/assignment3/derived/
      tables/field_year_aligned_<grower>_<field>_<year>.csv
      reports/field_year_dashboard_<grower>_<field>_<year>.png
      reports/field_year_coverage_<grower>_<field>_<year>.md
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
from datetime import date, datetime, timedelta
from pathlib import Path

import geopandas as gpd
import matplotlib
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import rasterio
from matplotlib.patches import FancyBboxPatch
from scipy.ndimage import uniform_filter1d

matplotlib.use("Agg")

_LOCAL_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(_LOCAL_LIB))

from paths import (
    SHARED_ROOT,
    farm_boundary_path,
    farm_cdl_full_composition_path,
    farm_weather_path,
    field_dir,
)
from runtime_paths import resolve_runtime_paths

_RUNTIME_PATHS = resolve_runtime_paths()
_GROWERS_ROOT = _RUNTIME_PATHS.runtime_base / "growers"

GDD_BASE_CORN = 10.0
GDD_BASE_SOYBEAN = 10.0
GDD_UPPER_CAP = 30.0

CROP_GDD_BASES = {
    "Corn": GDD_BASE_CORN,
    "Soybeans": GDD_BASE_SOYBEAN,
}

OUTPUT_BASE = SHARED_ROOT / "assignment3" / "derived"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Field-year dashboard prototype")
    p.add_argument("--grower-slug", required=True)
    p.add_argument("--farm-slug", required=True)
    p.add_argument("--field-id", required=True)
    p.add_argument("--year", type=int, default=2024)
    p.add_argument("--all-years", action="store_true", help="Generate dashboards for all 2021-2025")
    p.add_argument("--output-dir", default=None, help="Override output directory")
    return p.parse_args(argv)


def load_cdl_crop(
    grower_slug: str, farm_slug: str, field_id: str, year: int
) -> str | None:
    path = farm_cdl_full_composition_path(grower_slug, farm_slug)
    if not path.exists():
        print(f"  WARNING: CDL composition not found at {path}")
        return None
    df = pd.read_csv(path)
    subset = df[(df["field_id"] == field_id) & (df["year"] == year)]
    if subset.empty:
        print(f"  WARNING: No CDL record for {field_id} in {year}")
        return None
    dominant = subset.loc[subset.groupby("field_id")["pct"].idxmax()]
    return dominant["crop_name"].values[0] if not dominant.empty else None


def load_boundary(grower_slug: str, farm_slug: str, field_id: str) -> gpd.GeoDataFrame | None:
    path = farm_boundary_path(grower_slug, farm_slug)
    if not path.exists():
        return None
    all_fields = gpd.read_file(path)
    match = all_fields[all_fields["field_id"] == field_id]
    return match if not match.empty else None


def extract_ndvi(grower_slug: str, farm_slug: str, field_id: str, year: int) -> pd.DataFrame:
    records = []
    field_root = field_dir(grower_slug, farm_slug, field_id)
    for sensor in ("landsat", "sentinel"):
        sensor_dir = field_root / "satellite" / sensor / str(year)
        if not sensor_dir.exists():
            continue
        for scene_dir in sorted(sensor_dir.iterdir()):
            if not scene_dir.is_dir():
                continue
            ndvi_files = list(scene_dir.glob(f"*ndvi.tif"))
            if not ndvi_files:
                continue
            ndvi_path = ndvi_files[0]
            date_str = scene_dir.name.replace(f"{sensor}_", "")
            try:
                obs_date = datetime.strptime(date_str, "%Y%m%d").date()
            except ValueError:
                continue
            try:
                with rasterio.open(ndvi_path) as src:
                    data = src.read(1).astype("float32")
                    valid = data[np.isfinite(data)]
                    mean_ndvi = float(np.nanmean(valid)) if len(valid) > 0 else None
                    if mean_ndvi is not None:
                        records.append({
                            "date": obs_date,
                            "sensor": sensor,
                            "mean_ndvi": round(mean_ndvi, 4),
                            "valid_pixels": len(valid),
                            "total_pixels": data.size,
                        })
            except Exception as e:
                print(f"  WARNING: Failed to read {ndvi_path}: {e}")
    return pd.DataFrame(records)


def load_weather(grower_slug: str, farm_slug: str, field_id: str, year: int) -> pd.DataFrame:
    path = farm_weather_path(grower_slug, farm_slug)
    if not path.exists():
        return pd.DataFrame()
    all_weather = pd.read_csv(path, parse_dates=["date"])
    field_wx = all_weather[all_weather["field_id"] == field_id].copy()
    if field_wx.empty:
        return pd.DataFrame()
    field_wx["date"] = field_wx["date"].dt.date
    start = date(year, 1, 1)
    end = date(year, 12, 31)
    field_wx = field_wx[(field_wx["date"] >= start) & (field_wx["date"] <= end)]
    return field_wx.reset_index(drop=True)


def calc_gdd(tmax: float, tmin: float, base: float, upper_cap: float = GDD_UPPER_CAP) -> float:
    tmax_capped = min(tmax, upper_cap) if upper_cap else tmax
    tmin_eff = max(tmin, base) if base else tmin
    avg = (tmax_capped + tmin_eff) / 2
    return max(0.0, avg - base)


def build_aligned_table(
    ndvi: pd.DataFrame, weather: pd.DataFrame, crop: str | None, season_start: date, season_end: date
) -> pd.DataFrame:
    base_temp = CROP_GDD_BASES.get(crop, GDD_BASE_CORN) if crop else GDD_BASE_CORN
    wx = weather.set_index("date") if not weather.empty else pd.DataFrame()

    rows = []
    current = season_start
    cum_gdd = 0.0
    while current <= season_end:
        gdd = 0.0
        tmax = None
        tmin = None
        tmean = None
        precip = None
        solar = None
        rh = None
        ws = None
        if current in wx.index:
            row = wx.loc[current]
            tmax = float(row["T2M_MAX"])
            tmin = float(row["T2M_MIN"])
            tmean = float(row["T2M"])
            precip = float(row["PRECTOTCORR"])
            solar = float(row.get("ALLSKY_SFC_SW_DWN", None))
            rh = float(row.get("RH2M", None))
            ws = float(row.get("WS10M", None))
            gdd = calc_gdd(tmax, tmin, base_temp)
        cum_gdd += gdd
        ndvi_row = ndvi[ndvi["date"] == current]
        mean_ndvi = float(ndvi_row["mean_ndvi"].values[0]) if not ndvi_row.empty else None
        sensor = str(ndvi_row["sensor"].values[0]) if not ndvi_row.empty else None

        rows.append({
            "date": current,
            "crop": crop or "Unknown",
            "tmax_c": tmax,
            "tmin_c": tmin,
            "tmean_c": tmean,
            "precip_mm": precip,
            "solar_mj": solar,
            "rh_pct": rh,
            "ws_ms": ws,
            "gdd_daily": round(gdd, 2),
            "gdd_cumulative": round(cum_gdd, 2),
            "mean_ndvi": mean_ndvi,
            "sensor": sensor,
        })
        current += timedelta(days=1)
    return pd.DataFrame(rows)


def detect_events(aligned: pd.DataFrame) -> list[dict]:
    events = []
    ndvi_obs = aligned.dropna(subset=["mean_ndvi"]).copy()
    ndvi_vals = ndvi_obs["mean_ndvi"].values
    ndvi_dates = ndvi_obs["date"].values

    if len(ndvi_vals) >= 2:
        diffs = np.diff(ndvi_vals)
        for i in range(len(diffs)):
            d = ndvi_dates[i + 1]
            if diffs[i] > 0.15:
                events.append({"type": "rapid_increase", "date": d, "value": float(ndvi_vals[i + 1]),
                               "label": "Rapid green-up"})
            elif diffs[i] < -0.25:
                events.append({"type": "ndvi_decline", "date": d, "value": float(ndvi_vals[i + 1]),
                               "label": "Late-season decline"})
            elif diffs[i] < -0.15:
                events.append({"type": "ndvi_dip", "date": d, "value": float(ndvi_vals[i + 1]),
                               "label": "NDVI dip"})

    if len(ndvi_vals) > 0:
        peak_idx = int(np.argmax(ndvi_vals))
        events.append({"type": "peak_ndvi", "date": ndvi_dates[peak_idx],
                       "value": float(ndvi_vals[peak_idx]), "label": "Peak canopy"})

    Apr1 = date(aligned["date"].iloc[0].year, 4, 1)
    greenups = aligned[(aligned["date"] >= Apr1) & (aligned["mean_ndvi"] > 0.3)]
    if not greenups.empty:
        first = greenups.iloc[0]
        events.append({"type": "greenup", "date": first["date"],
                       "value": float(first["mean_ndvi"]), "label": "Green-up"})

    for _, row in aligned.iterrows():
        d = row["date"]
        if row["precip_mm"] is not None and row["precip_mm"] > 20:
            events.append({"type": "heavy_rain", "date": d, "value": float(row["precip_mm"]),
                           "label": "Heavy rain"})
        if row["tmax_c"] is not None and row["tmax_c"] > 32:
            events.append({"type": "hot_day", "date": d, "value": float(row["tmax_c"]),
                           "label": "Hot day"})

    season = aligned[(aligned["date"] >= Apr1) & (aligned["date"] <= date(Apr1.year, 9, 30))]
    cool = season[season["tmean_c"] < 10.0]
    if not cool.empty:
        events.append({"type": "cool_period", "date": cool["date"].iloc[0],
                       "value": float(cool["tmean_c"].iloc[0]), "label": "Cool period"})

    ndvi_gap = aligned.dropna(subset=["mean_ndvi"])
    if len(ndvi_gap) >= 2:
        max_gap = 0
        max_gap_end = None
        for i in range(len(ndvi_gap) - 1):
            gap = (ndvi_gap["date"].iloc[i + 1] - ndvi_gap["date"].iloc[i]).days
            if gap > max_gap:
                max_gap = gap
                max_gap_end = ndvi_gap["date"].iloc[i + 1]
        if max_gap > 40:
            events.append({"type": "ndvi_gap", "date": max_gap_end,
                           "value": max_gap, "label": f"NDVI gap: {max_gap}d"})

    in_season = aligned[(aligned["date"] >= date(Apr1.year, 5, 1)) & (aligned["date"] <= date(Apr1.year, 9, 30))]
    dry_streak = 0
    for _, row in in_season.iterrows():
        p = row["precip_mm"] or 0
        if p < 1:
            dry_streak += 1
            if dry_streak == 10:
                events.append({"type": "dry_gap", "date": row["date"],
                               "value": dry_streak, "label": f"Dry gap {dry_streak}d"})
        else:
            dry_streak = 0

    if aligned["gdd_daily"].notna().sum() > 30:
        window = 7
        gdd_roll = aligned["gdd_daily"].rolling(window, center=True).mean().copy()
        avg_rate = gdd_roll.mean()
        threshold = avg_rate * 0.5
        slow = gdd_roll < threshold
        in_slow = False
        slow_start = None
        for i, (date_val, is_slow) in enumerate(zip(aligned["date"], slow)):
            if is_slow and not in_slow:
                in_slow = True
                slow_start = date_val
            elif not is_slow and in_slow:
                if (date_val - slow_start).days >= 7:
                    events.append({"type": "gdd_slowdown", "date": slow_start,
                                   "value": float(gdd_roll.loc[aligned["date"] == slow_start].iloc[0])
                                   if not aligned[aligned["date"] == slow_start].empty else 0.0,
                                   "label": "GDD slowdown"})
                in_slow = False
        if in_slow and (aligned["date"].iloc[-1] - slow_start).days >= 7:
            events.append({"type": "gdd_slowdown", "date": slow_start,
                           "value": float(gdd_roll.loc[aligned["date"] == slow_start].iloc[0])
                           if not aligned[aligned["date"] == slow_start].empty else 0.0,
                           "label": "GDD slowdown"})

    return events


def _annotated_event_descriptions(events: list[dict]) -> list[str]:
    descriptions = []
    hot_count = len([e for e in events if e["type"] == "hot_day"])
    rain_sorted = sorted([e for e in events if e["type"] == "heavy_rain"], key=lambda x: x["value"], reverse=True)
    top_rain = {e["date"] for e in rain_sorted[:2]}
    for ev in events:
        t = ev["type"]
        d = ev["date"]
        v = ev["value"]
        if t == "rapid_increase":
            descriptions.append(f"- **Rapid green-up**: NDVI rose sharply around {d} (jump of {v:.2f})")
        elif t == "peak_ndvi":
            descriptions.append(f"- **Peak canopy**: NDVI {v:.2f} on {d}")
        elif t == "ndvi_decline":
            descriptions.append(f"- **Late-season decline**: NDVI dropped around {d} (to {v:.2f})")
        elif t == "ndvi_gap":
            descriptions.append(f"- **Major NDVI gap**: {v:.0f} days without observations ending {d}")
        elif t == "heavy_rain" and d in top_rain:
            descriptions.append(f"- **Heavy rain**: {v:.0f} mm on {d}")
        elif t == "dry_gap":
            descriptions.append(f"- **Dry period**: {v:.0f} days with <1 mm ending {d}")
        elif t == "hot_day" and hot_count >= 5:
            continue
    return descriptions


def print_coverage_report(aligned: pd.DataFrame, events: list[dict], output_dir: Path,
                          grower_slug: str, field_id: str, year: int):
    ndvi_obs = aligned.dropna(subset=["mean_ndvi"])
    total_days = len(aligned)
    ndvi_count = len(ndvi_obs)
    sensors = ndvi_obs["sensor"].value_counts().to_dict() if ndvi_count > 0 else {}

    missing_wx = aligned["tmean_c"].isna().sum()
    total_precip = aligned["precip_mm"].sum()
    total_gdd = aligned["gdd_daily"].sum()
    crop = aligned['crop'].iloc[0] if not aligned.empty else 'Unknown'
    peak_ndvi = ndvi_obs['mean_ndvi'].max() if ndvi_count > 0 else 0
    peak_date = ndvi_obs.loc[ndvi_obs['mean_ndvi'].idxmax(), 'date'] if ndvi_count > 0 else ""

    ndvi_gaps = [e for e in events if e["type"] == "ndvi_gap"]
    missing_warnings = []
    if ndvi_gaps:
        for ev in ndvi_gaps:
            missing_warnings.append(f"- NDVI gap: {ev['value']:.0f} days without observations (ending {ev['date']})")

    # Build main annotated event descriptions
    ann_descs = _annotated_event_descriptions(events)

    desc_lines = []
    # Use dict to deduplicate descriptions
    seen_desc = set()
    for desc in ann_descs:
        if desc not in seen_desc:
            seen_desc.add(desc)
            desc_lines.append(desc)

    lines = [
        f"# Field Dashboard Summary",
        f"",
        f"- **Field:** {field_id}",
        f"- **Year:** {year}",
        f"- **CDL Crop:** {crop}",
        f"- **NDVI observations:** {ndvi_count} ({', '.join(f'{k}={v}' for k,v in sensors.items())})",
        f"- **Total precipitation:** {total_precip:.0f} mm ({total_precip/25.4:.1f} in)",
        f"- **Final cumulative GDD:** {total_gdd:.0f} °C-days",
        f"- **Peak NDVI:** {peak_ndvi:.2f} on {peak_date}",
        f"",
        f"## Main Annotated Events",
    ]
    if desc_lines:
        lines.extend(desc_lines)
    else:
        lines.append("- No significant events detected.")

    lines.extend([
        f"",
        f"## Data Quality Warnings",
    ])
    if missing_warnings:
        lines.extend(missing_warnings)
    else:
        lines.append("- No major data gaps detected.")
    if missing_wx > 0:
        lines.append(f"- Weather data missing for {missing_wx} days")

    lines.extend([
        f"",
        f"## All Detected Events ({len(events)})",
    ])
    for ev in sorted(events, key=lambda x: x["date"]):
        lines.append(f"- {ev['date']}: {ev['label']} ({ev['value']})")

    coverage_path = output_dir / "reports" / f"field_year_coverage_{grower_slug}_{field_id}_{year}.md"
    coverage_path.parent.mkdir(parents=True, exist_ok=True)
    coverage_path.write_text("\n".join(lines) + "\n")
    print(f"  md   {coverage_path}")

    print(f"\n{'='*60}")
    print(f"Coverage Summary: {field_id}, {year}")
    print(f"  NDVI: {ndvi_count} observations ({', '.join(f'{k}={v}' for k,v in sensors.items())})")
    print(f"  Weather: {total_days - missing_wx}/{total_days} days complete")
    print(f"  Total precip: {total_precip:.0f} mm | Total GDD: {total_gdd:.0f}")
    print(f"  Events detected: {len(events)}")
    print(f"{'='*60}")


def _build_narrative(aligned: pd.DataFrame, events: list[dict]) -> str:
    crop = aligned["crop"].iloc[0] if not aligned.empty else "Crop"
    peak_ev = [e for e in events if e["type"] == "peak_ndvi"]
    rapid_ev = [e for e in events if e["type"] == "rapid_increase"]
    decline_ev = [e for e in events if e["type"] == "ndvi_decline"]
    ndvi = aligned.dropna(subset=["mean_ndvi"])
    total_gdd = int(aligned["gdd_daily"].sum()) if not aligned.empty else 0

    peak_month = ""
    peak_val = 0
    if peak_ev:
        d = peak_ev[0]["date"]
        if hasattr(d, "strftime"):
            peak_month = d.strftime("%B")
        else:
            peak_month = str(d).split("-")[1]
        peak_val = peak_ev[0]["value"]

    greenup_month = ""
    if rapid_ev:
        d = rapid_ev[0]["date"]
        if hasattr(d, "strftime"):
            greenup_month = d.strftime("%B")
        else:
            greenup_month = str(d).split("-")[1]

    decline_month = ""
    if decline_ev:
        d = decline_ev[0]["date"]
        if hasattr(d, "strftime"):
            decline_month = d.strftime("%B")
        else:
            decline_month = str(d).split("-")[1]

    narrative = (
        f"{crop} NDVI increased rapidly through {greenup_month}–{peak_month}, "
        f"peaked in {peak_month} (NDVI {peak_val:.2f}), "
        f"and declined after {decline_month} "
        f"while cumulative GDD approached its seasonal plateau at {total_gdd} °C-days."
    )
    return narrative


def _dedup_events(events: list[dict]) -> list[dict]:
    seen = set()
    deduped = []
    for ev in events:
        key = (ev["type"], ev["date"])
        if key not in seen:
            seen.add(key)
            deduped.append(ev)
    return deduped


def _select_crosshair_events(events):
    priority = ["peak_ndvi", "rapid_increase", "ndvi_decline"]
    chosen = []
    for p in priority:
        ev = [e for e in events if e["type"] == p]
        if ev:
            chosen.append(ev[0])
        if len(chosen) >= 3:
            break
    if not chosen and events:
        chosen = [events[0]]
    return chosen


def plot_dashboard(aligned: pd.DataFrame, events: list[dict], boundary: gpd.GeoDataFrame | None,
                   output_path: Path):
    ndvi = aligned.dropna(subset=["mean_ndvi"]).copy()
    events = _dedup_events(events)

    fig, (ax1, ax2, ax3, ax4) = plt.subplots(4, 1, figsize=(16, 12), sharex=True,
                                              gridspec_kw={"height_ratios": [3, 2, 2, 2], "hspace": 0.10})

    dates_all = pd.to_datetime(aligned["date"])
    dates_ndvi = pd.to_datetime(ndvi["date"])

    # ── Metadata ────────────────────────────────────────────────────────
    crop = aligned["crop"].iloc[0] if not aligned.empty else "?"
    fid = aligned["field_id"].iloc[0] if "field_id" in aligned.columns else ""
    yr = aligned["year"].iloc[0] if "year" in aligned.columns else ""
    ndvi_count = len(aligned.dropna(subset=["mean_ndvi"]))
    total_precip = aligned["precip_mm"].sum()
    total_gdd = aligned["gdd_daily"].sum()
    peak_ndvi = aligned["mean_ndvi"].max()
    peak_date = aligned.loc[aligned["mean_ndvi"].idxmax(), "date"] if not aligned["mean_ndvi"].isna().all() else ""

    # ── Cross-panel vertical event lines ───────────────────────────────
    crosshair_events = _select_crosshair_events(events)
    for ev in crosshair_events:
        d = pd.Timestamp(ev["date"])
        for ax in (ax1, ax2, ax3, ax4):
            ax.axvline(d, color="#6b7280", linewidth=0.8, linestyle="--", alpha=0.35, zorder=1)

    # ══════════════════════════════════════════════════════════════════
    # Panel 1: NDVI
    # ══════════════════════════════════════════════════════════════════
    landsat = ndvi[ndvi["sensor"] == "landsat"]
    sentinel = ndvi[ndvi["sensor"] == "sentinel"]

    ax1.scatter(pd.to_datetime(landsat["date"]), landsat["mean_ndvi"],
                c="#2563eb", s=60, edgecolors="white", linewidth=0.8, zorder=5, label="Landsat")
    ax1.scatter(pd.to_datetime(sentinel["date"]), sentinel["mean_ndvi"],
                c="#ea580c", s=60, marker="^", edgecolors="white", linewidth=0.8, zorder=5, label="Sentinel")

    if len(ndvi) >= 4:
        order = np.argsort(ndvi["date"])
        x_smooth = np.arange(len(ndvi))
        y_smooth = ndvi["mean_ndvi"].values[order]
        window = max(3, len(ndvi) // 5)
        if window % 2 == 0:
            window += 1
        smoothed = uniform_filter1d(y_smooth, size=window, mode="nearest")
        ax1.plot(pd.to_datetime(ndvi["date"].values[order]), smoothed,
                 color="#4b5563", linewidth=1.5, linestyle="--", alpha=0.7, label="Smoothed trend")

    # bare-soil reference line
    ax1.axhline(y=0.3, color="#9ca3af", linewidth=0.8, linestyle=":", alpha=0.5)
    ax1.text(dates_all.iloc[0], 0.31, "bare soil", fontsize=7, color="#6b7280", va="bottom", alpha=0.7)

    # NDVI gap shaded region
    gap_ev = [e for e in events if e["type"] == "ndvi_gap"]
    for ev in gap_ev:
        d = pd.Timestamp(ev["date"])
        ax1.axvspan(d - pd.Timedelta(days=ev["value"]), d, alpha=0.08, color="#dc2626")
        ax1.annotate(f"NDVI gap: {ev['value']:.0f}d", xy=(d, 0.05),
                     fontsize=7.5, color="#dc2626", ha="right", fontweight="bold",
                     bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.7))

    # NDVI annotations (limited to key events, inline style)
    rapid_ev = [e for e in events if e["type"] == "rapid_increase"]
    for ev in rapid_ev[:1]:
        d = pd.Timestamp(ev["date"])
        ax1.annotate("Rapid green-up", xy=(d, ev["value"]),
                     xytext=(d, ev["value"] - 0.15), textcoords="data",
                     fontsize=7.5, color="#ca8a04", fontweight="bold", ha="center",
                     arrowprops=dict(arrowstyle="->", color="#ca8a04", lw=0.8),
                     bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.8))

    peak_ev = [e for e in events if e["type"] == "peak_ndvi"]
    for ev in peak_ev:
        d = pd.Timestamp(ev["date"])
        ax1.annotate(f"Peak: NDVI {ev['value']:.2f}", xy=(d, ev["value"]),
                     xytext=(d, ev["value"] - 0.15), textcoords="data",
                     fontsize=7.5, fontweight="bold", color="#16a34a", ha="center",
                     arrowprops=dict(arrowstyle="->", color="#16a34a", lw=0.8),
                     bbox=dict(boxstyle="round,pad=0.12", fc="white", ec="none", alpha=0.8))

    ax1.set_ylabel("NDVI", fontsize=11)
    ax1.set_ylim(-0.05, 1.05)
    ax1.legend(loc="upper left", fontsize=8, framealpha=0.85)
    ax1.grid(axis="y", alpha=0.25)
    ax1.grid(axis="x", alpha=0.12, which="both")
    ax1.tick_params(labelbottom=False)

    # ══════════════════════════════════════════════════════════════════
    # Panel 2: Precipitation
    # ══════════════════════════════════════════════════════════════════
    ax2.bar(dates_all, aligned["precip_mm"], width=0.8, color="#60a5fa", alpha=0.7, label="Daily precip")

    # Annotate the top 2 rainfall events
    rain_ev = sorted([e for e in events if e["type"] == "heavy_rain"], key=lambda x: x["value"], reverse=True)
    for i, ev in enumerate(rain_ev[:2]):
        d = pd.Timestamp(ev["date"])
        y = ev["value"]
        ax2.scatter([d], [y], c="#16a34a", s=40, zorder=6, marker="o",
                    label="Heavy rain" if i == 0 else "")
        ax2.annotate(f"{d.strftime('%d %b')}: {y:.0f}mm", xy=(d, y),
                     xytext=(5, 0), textcoords="offset points",
                     fontsize=7, color="#16a34a", ha="left", va="center",
                     fontweight="bold")

    ax2.set_ylabel("Precipitation (mm)", fontsize=11)
    ax2.legend(loc="upper left", fontsize=8, framealpha=0.85)
    ax2.grid(axis="y", alpha=0.25)
    ax2.grid(axis="x", alpha=0.12, which="both")
    ax2.tick_params(labelbottom=False)

    # ══════════════════════════════════════════════════════════════════
    # Panel 3: Temperature
    # ══════════════════════════════════════════════════════════════════
    ax3.plot(dates_all, aligned["tmax_c"], color="#dc2626", linewidth=0.8, alpha=0.7, label="Tmax")
    ax3.plot(dates_all, aligned["tmin_c"], color="#2563eb", linewidth=0.8, alpha=0.7, label="Tmin")
    ax3.plot(dates_all, aligned["tmean_c"], color="#1f2937", linewidth=1.2, alpha=0.9, label="Tmean")
    ax3.fill_between(dates_all, aligned["tmin_c"], aligned["tmax_c"], alpha=0.06, color="#6b7280")
    ax3.axhline(y=10, color="#9ca3af", linewidth=0.8, linestyle=":", alpha=0.4)
    ax3.text(dates_all.iloc[0], 10.5, "GDD base 10°C", fontsize=6.5, color="#6b7280", va="bottom", alpha=0.6)

    hot_ev = [e for e in events if e["type"] == "hot_day"]
    if len(hot_ev) >= 5:
        hot_dates_sorted = sorted(pd.Timestamp(e["date"]) for e in hot_ev)
        ax3.axvspan(hot_dates_sorted[0], hot_dates_sorted[-1], alpha=0.06, color="#dc2626")
        ax3.annotate(f"Hot period ({len(hot_ev)} days >32°C)",
                     xy=(hot_dates_sorted[len(hot_dates_sorted)//2], aligned["tmax_c"].max() * 0.95),
                     fontsize=7.5, color="#dc2626", ha="center", fontweight="bold",
                     bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.7))
    elif hot_ev:
        hot_dates = [pd.Timestamp(e["date"]) for e in hot_ev]
        hot_vals = [e["value"] for e in hot_ev]
        ax3.scatter(hot_dates, hot_vals, c="#dc2626", s=25, zorder=6, marker="o",
                    label="Hot day (>32°C)")

    ax3.set_ylabel("Temperature (°C)", fontsize=11)
    ax3.legend(loc="upper left", fontsize=8, framealpha=0.85)
    ax3.grid(axis="y", alpha=0.25)
    ax3.grid(axis="x", alpha=0.12, which="both")
    ax3.tick_params(labelbottom=False)

    # ══════════════════════════════════════════════════════════════════
    # Panel 4: Cumulative GDD
    # ══════════════════════════════════════════════════════════════════
    ax4.plot(dates_all, aligned["gdd_cumulative"], color="#16a34a", linewidth=1.8, label="Cumulative GDD")
    last_valid = aligned.dropna(subset=["gdd_cumulative"])
    if not last_valid.empty:
        final_date = pd.to_datetime(last_valid["date"].iloc[-1])
        final_gdd = last_valid["gdd_cumulative"].iloc[-1]
        ax4.annotate(f"Total: {final_gdd:.0f} °C-days",
                     xy=(0.95, 0.05), xycoords="axes fraction",
                     fontsize=8, color="#16a34a", fontweight="bold",
                     ha="right", va="bottom",
                     bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="#16a34a", alpha=0.8))

    # Limit GDD slowdown to longest only
    gdd_slow = [e for e in events if e["type"] == "gdd_slowdown"]
    if gdd_slow:
        slow_vals = []
        for ev in gdd_slow:
            row = aligned.loc[aligned["date"] == ev["date"]]
            if not row.empty:
                slow_vals.append((ev, float(row["gdd_cumulative"].values[0])))
        if slow_vals:
            ev, y = max(slow_vals, key=lambda x: x[1])
            d = pd.Timestamp(ev["date"])
            ax4.annotate("GDD slowdown", xy=(d, y),
                         xytext=(8, -10), textcoords="offset fontsize",
                         fontsize=7, color="#16a34a",
                         arrowprops=dict(arrowstyle="->", color="#16a34a", lw=0.6))

    ax4.set_ylabel("Cumulative GDD (°C·days)", fontsize=11)
    ax4.set_xlabel("Date", fontsize=11)
    ax4.legend(loc="upper left", fontsize=8, framealpha=0.85)
    ax4.grid(axis="y", alpha=0.25)
    ax4.grid(axis="x", alpha=0.12, which="both")

    # ── Shared x-axis ────────────────────────────────────────────────────
    ax4.xaxis.set_major_locator(mdates.MonthLocator())
    ax4.xaxis.set_major_formatter(mdates.DateFormatter("%b"))
    ax4.xaxis.set_minor_locator(mdates.WeekdayLocator(interval=2))
    for label in ax4.get_xticklabels():
        label.set_fontsize(9)

    # ── Titles ───────────────────────────────────────────────────────────
    fig.suptitle(f"Field {fid} — {yr} {crop}",
                 fontsize=13, fontweight="bold", y=0.98, x=0.5)
    subtitle = (
        f"{ndvi_count} NDVI observations  |  "
        f"Total precipitation: {total_precip:.0f} mm  |  "
        f"Seasonal GDD: {total_gdd:.0f} °C-days  |  "
        f"Peak NDVI: {peak_ndvi:.2f} on {peak_date}"
    )
    fig.text(0.5, 0.94, subtitle, ha="center", fontsize=9, color="#4b5563")

    fig.subplots_adjust(top=0.91, bottom=0.07)

    narrative = _build_narrative(aligned, events)
    fig.text(0.5, 0.02, narrative, ha="center", fontsize=8, color="#4b5563",
             fontstyle="italic", wrap=True)

    fig.savefig(output_path, dpi=300, bbox_inches="tight", pad_inches=0.3)
    plt.close(fig)
    print(f"  png  {output_path}")


def save_aligned_table(aligned: pd.DataFrame, output_dir: Path, grower_slug: str, field_id: str, year: int):
    out = output_dir / "tables" / f"field_year_aligned_{grower_slug}_{field_id}_{year}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    aligned.to_csv(out, index=False)
    print(f"  csv  {out}")


def run_one(grower_slug: str, farm_slug: str, field_id: str, year: int, output_dir: Path):
    print(f"\n{'='*60}")
    print(f"Field-Year Dashboard: {grower_slug} / {farm_slug} / {field_id} / {year}")
    print(f"{'='*60}")

    print("\n[1/6] Loading CDL crop identification...")
    crop = load_cdl_crop(grower_slug, farm_slug, field_id, year)
    if crop:
        print(f"  Crop: {crop}")
    else:
        print(f"  No CDL crop found; proceeding without crop-specific GDD base.")

    print("\n[2/6] Loading field boundary...")
    boundary = load_boundary(grower_slug, farm_slug, field_id)
    if boundary is not None:
        print(f"  Found boundary ({boundary['area_acres'].values[0]:.1f} ac)" if 'area_acres' in boundary.columns else "  Found boundary")
    else:
        print(f"  No boundary found.")

    print("\n[3/6] Extracting NDVI rasters...")
    ndvi = extract_ndvi(grower_slug, farm_slug, field_id, year)
    if not ndvi.empty:
        print(f"  {len(ndvi)} NDVI observations ({', '.join(f'{k}={v}' for k,v in ndvi['sensor'].value_counts().to_dict().items())})")
        print(f"  Date range: {ndvi['date'].min()} to {ndvi['date'].max()}")
        print(f"  Mean NDVI: {ndvi['mean_ndvi'].mean():.3f}, Peak: {ndvi['mean_ndvi'].max():.3f}")
    else:
        print(f"  No NDVI data found.")

    print("\n[4/6] Loading weather records...")
    weather = load_weather(grower_slug, farm_slug, field_id, year)
    if not weather.empty:
        print(f"  {len(weather)} daily weather records")
        print(f"  Date range: {weather['date'].min()} to {weather['date'].max()}")
    else:
        print(f"  No weather data found.")

    print("\n[5/6] Building aligned table and detecting events...")
    season_start = date(year, 3, 1)
    season_end = date(year, 11, 30)
    aligned = build_aligned_table(ndvi, weather, crop, season_start, season_end)
    aligned["field_id"] = field_id
    aligned["year"] = year
    print(f"  Aligned table: {len(aligned)} days")
    print(f"  NDVI observations: {aligned['mean_ndvi'].notna().sum()}")
    print(f"  Weather days: {aligned['tmean_c'].notna().sum()}")

    events = detect_events(aligned)
    print(f"  Events detected: {len(events)}")

    print("\n[6/6] Generating outputs...")
    output_dir.mkdir(parents=True, exist_ok=True)
    save_aligned_table(aligned, output_dir, grower_slug, field_id, year)

    report_dir = output_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)

    dash_path = report_dir / f"field_year_dashboard_{grower_slug}_{field_id}_{year}.png"
    plot_dashboard(aligned, events, boundary, dash_path)

    print_coverage_report(aligned, events, output_dir, grower_slug, field_id, year)

    print(f"\n✓ Done ({year}). See {output_dir}")
    return aligned, events


def main():
    global args
    args = parse_args()

    output_dir = Path(args.output_dir) if args.output_dir else OUTPUT_BASE

    if args.all_years:
        for y in range(2021, 2026):
            run_one(args.grower_slug, args.farm_slug, args.field_id, y, output_dir)
    else:
        run_one(args.grower_slug, args.farm_slug, args.field_id, args.year, output_dir)


if __name__ == "__main__":
    main()
