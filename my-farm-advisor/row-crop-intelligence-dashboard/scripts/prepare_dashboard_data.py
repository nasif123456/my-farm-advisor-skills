#!/usr/bin/env python3
"""Prepare dashboard-ready files from integrated and scored field data."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.metrics import compute_all_metrics
from app.interpretations import generate_field_summary, generate_grower_summary, generate_key_findings
from scripts.integrate_data import integrate_grower

log = logging.getLogger(__name__)


def prepare(runtime_dir: str, grower_id: str, year: int, output_dir: str) -> dict:
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    integrated = integrate_grower(runtime_dir, grower_id, year)
    scored = compute_all_metrics(integrated)

    summary_path = out / "field_summary.csv"
    scored.to_csv(summary_path, index=False)
    print(f"Field summary: {len(scored)} fields -> {summary_path}")

    boundaries = None
    from scripts.integrate_data import discover_farms, load_field_boundaries
    farms = discover_farms(runtime_dir, grower_id)
    all_boundaries = []
    for farm_dir in farms:
        try:
            b = load_field_boundaries(farm_dir)
            farm_name = farm_dir.name
            b["farm_name"] = farm_name
            all_boundaries.append(b)
        except Exception as e:
            print(f"Warning: could not load boundaries for {farm_dir.name}: {e}")
    if all_boundaries:
        combined = pd.concat(all_boundaries, ignore_index=True)
        geo_path = out / "field_boundaries.geojson"
        combined.to_file(geo_path, driver="GeoJSON")
        print(f"Boundaries: {len(combined)} fields -> {geo_path}")

    fields_dir = None
    for farm_dir in farms:
        fd = farm_dir / "fields"
        if fd.exists():
            fields_dir = fd
            break

    if fields_dir is not None and not scored.empty:
        field_ids = scored["field_id"].tolist()
        all_ndvi = []
        all_weather = []
        for fid in field_ids:
            ndvi_csv = fields_dir / fid / "derived" / "tables" / "ndvi_year_crop_join.csv"
            if ndvi_csv.exists():
                ndf = pd.read_csv(ndvi_csv)
                ndf["field_id"] = fid
                all_ndvi.append(ndf)
            wthr_csv = fields_dir / fid / "weather" / "daily_weather.csv"
            if wthr_csv.exists():
                wdf = pd.read_csv(wthr_csv)
                wdf["field_id"] = fid
                wdf["date"] = pd.to_datetime(wdf["date"])
                wdf = wdf[wdf["date"].dt.year == year]
                if not wdf.empty:
                    all_weather.append(wdf)

        if all_ndvi:
            ndvi_df = pd.concat(all_ndvi, ignore_index=True)
            ndvi_df.to_csv(out / "ndvi_timeseries.csv", index=False)
        if all_weather:
            weather_df = pd.concat(all_weather, ignore_index=True)
            weather_df.to_csv(out / "weather_timeseries.csv", index=False)

    field_interpretations = {}
    for fid in scored["field_id"]:
        field_interpretations[fid] = generate_field_summary(scored, fid)

    interp = {
        "grower_summary": generate_grower_summary(scored),
        "key_findings": generate_key_findings(scored),
        "per_field": field_interpretations,
    }
    with open(out / "interpretations.json", "w") as f:
        json.dump(interp, f, indent=2)

    soil_path = out / "soil_summary.csv"
    soil_keywords = ("soil_", "organic", "drainage", "available",
                     "cec", "clay", "sand", "erosion",
                     "om_score", "ph_", "awc_score", "cec_score",
                     "om_depth", "awc_profile", "dominant_mapunit",
                     "k_factor", "ssurgo_", "awc_validation",
                     "soil_aggregation")
    soil_cols = [c for c in scored.columns
                 if any(c.startswith(k) or c == k for k in soil_keywords)
                 or c == "field_id"]
    if soil_cols:
        soil_df = scored[[c for c in soil_cols if c in scored.columns]]
        soil_df.to_csv(soil_path, index=False)

    metadata = {
        "grower_id": grower_id,
        "year": year,
        "runtime_dir": runtime_dir,
        "field_count": len(scored),
        "generated_at": str(pd.Timestamp.now()),
        "scores_included": [c for c in scored.columns if c.endswith("_score")
                           or c in ("fis_score", "crop_stress_apparent",
                                   "conservation_priority_score",
                                   "ndvi_condition_score")],
    }
    with open(out / "dashboard_metadata.json", "w") as f:
        json.dump(metadata, f, indent=2)

    return metadata


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Prepare dashboard-ready data files")
    parser.add_argument("--grower-id", required=True)
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    meta = prepare(args.runtime_dir, args.grower_id, args.year, args.output)
    print(f"\nDashboard data prepared successfully.")
    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    sys.exit(main())
