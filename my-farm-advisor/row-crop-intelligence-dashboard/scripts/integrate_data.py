#!/usr/bin/env python3
"""Join field boundaries, NDVI, weather, and soil data into a unified field-year summary."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

log = logging.getLogger(__name__)


def discover_farms(runtime_dir: str, grower_id: str) -> list[Path]:
    base = Path(runtime_dir) / "data-pipeline" / "growers" / grower_id / "farms"
    if not base.exists():
        raise FileNotFoundError(f"Grower directory not found: {base}")
    return sorted(base.iterdir())


def load_field_boundaries(farm_dir: Path) -> gpd.GeoDataFrame:
    path = farm_dir / "boundary" / "field_boundaries.geojson"
    if not path.exists():
        raise FileNotFoundError(f"Boundary file not found: {path}")
    gdf = gpd.read_file(path)
    gdf = gdf.to_crs("EPSG:4326")
    if "area_acres" not in gdf.columns:
        gdf = gdf.to_crs("EPSG:5070")
        gdf["area_acres"] = gdf.geometry.area / 4046.86
        gdf = gdf.to_crs("EPSG:4326")
    return gdf


def _extract_mean_ndvi_from_scenes(fields_dir: Path, field_id: str, year: int) -> float | None:
    """Extract mean NDVI from per-scene NDVI TIFFs for a field and year."""
    import rasterio
    import numpy as np
    scene_base = fields_dir / field_id / "satellite" / "sentinel" / str(year)
    if not scene_base.exists():
        return None
    scene_dirs = sorted(scene_base.iterdir())
    ndvi_values = []
    for sd in scene_dirs:
        if not sd.is_dir():
            continue
        ndvi_tif = sd / f"{sd.name}_ndvi.tif"
        if not ndvi_tif.exists():
            continue
        try:
            with rasterio.open(ndvi_tif) as src:
                data = src.read(1).astype(np.float32)
                valid = data[(~np.isnan(data)) & (data >= -1) & (data <= 1)]
                if len(valid) > 0:
                    ndvi_values.append(float(valid.mean()))
        except Exception:
            continue
    if ndvi_values:
        return sum(ndvi_values) / len(ndvi_values)
    return None


def load_ndvi_summary(fields_dir: Path, field_ids: list[str], year: int) -> pd.DataFrame:
    rows = []
    for fid in field_ids:
        summary_path = fields_dir / fid / "derived" / "summaries" / "ndvi_yearly_summary.json"
        card_path = fields_dir / fid / "derived" / "summaries" / "ndvi_card_summary.json"
        row: dict[str, Any] = {"field_id": fid, "year": year}

        # Extract crop-neutral NDVI stats from card summary if available
        if card_path.exists():
            with open(card_path) as f:
                cards = json.load(f).get("cards", {})
            ndvi_vals = []
            peak_vals = []
            for card_key, card in cards.items():
                if card.get("status") == "available":
                    mn = card.get("mean_ndvi")
                    if mn is not None and isinstance(mn, (int, float)):
                        ndvi_vals.append(mn)
                    # Peak 95th percentile cards may have mean_ndvi or be under separate keys
                    if card_key.endswith("_peak_95"):
                        pk = card.get("mean_ndvi")
                        if pk is not None and isinstance(pk, (int, float)):
                            peak_vals.append(pk)
            if ndvi_vals:
                row["mean_ndvi"] = sum(ndvi_vals) / len(ndvi_vals)
            if peak_vals:
                row["peak_ndvi"] = sum(peak_vals) / len(peak_vals)

        if summary_path.exists():
            with open(summary_path) as f:
                years_data = json.load(f).get("years", [])
            for yd in years_data:
                if yd.get("year") == year:
                    row["crop_name"] = yd.get("crop_name")
                    row["scene_count"] = yd.get("scene_count")
                    row["ndvi_composite_tif"] = yd.get("composite_tif")
                    break

        if row.get("scene_count") is None:
            scene_base = fields_dir / fid / "satellite" / "sentinel" / str(year)
            if scene_base.exists():
                row["scene_count"] = sum(1 for p in scene_base.iterdir() if p.is_dir())
            else:
                row["scene_count"] = 0

        # Fallback: if no card data, try extracting from scene TIFFs directly
        if "mean_ndvi" not in row:
            mean_ndvi = _extract_mean_ndvi_from_scenes(fields_dir, fid, year)
            if mean_ndvi is not None:
                row["mean_ndvi"] = mean_ndvi

        rows.append(row)
    return pd.DataFrame(rows)


def load_weather(fields_dir: Path, field_ids: list[str], year: int) -> pd.DataFrame:
    records = []
    for fid in field_ids:
        csv_path = fields_dir / fid / "weather" / "daily_weather.csv"
        if not csv_path.exists():
            continue
        df = pd.read_csv(csv_path)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        df = df[df["date"].dt.year == year].copy()
        if df.empty:
            continue
        gdd_base = 10
        df["tavg"] = (df["T2M_MAX"] + df["T2M_MIN"]) / 2.0
        df["gdd"] = (df["tavg"] - gdd_base).clip(lower=0)
        records.append({
            "field_id": fid,
            "total_precipitation_mm": df["PRECTOTCORR"].sum(),
            "mean_temp_c": df["tavg"].mean(),
            "max_temp_c": df["T2M_MAX"].max(),
            "min_temp_c": df["T2M_MIN"].min(),
            "cumulative_gdd": df["gdd"].sum(),
            "dry_day_count": int((df["PRECTOTCORR"] < 0.5).sum()),
            "observation_days": len(df),
        })
    if not records:
        return pd.DataFrame()
    return pd.DataFrame(records)


def load_soil_summary(farm_dir: Path) -> pd.DataFrame | None:
    candidates = list((farm_dir / "derived" / "tables").glob("*ssurgo_summary*"))
    if candidates:
        df = pd.read_csv(candidates[0])
        rename = {
            "avg_om_pct": "organic_matter_pct",
            "avg_ph": "soil_ph",
            "total_aws_inches": "available_water_capacity_in",
            "avg_cec": "cec_meq100g",
            "avg_clay_pct": "clay_pct",
            "avg_sand_pct": "sand_pct",
            "drainage_class": "drainage_class",
            "dominant_soil": "dominant_soil",
            "dominant_mapunit_name": "dominant_mapunit_name",
            "dominant_mapunit_pct": "dominant_mapunit_pct",
            "erosion_risk": "erosion_risk",
            "k_factor": "k_factor",
            "erosion_evidence_source": "erosion_evidence_source",
            "om_depth_cm": "om_depth_cm",
            "ph_depth_cm": "ph_depth_cm",
            "cec_depth_cm": "cec_depth_cm",
            "awc_profile_depth_cm": "awc_profile_depth_cm",
        }
        df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
        cols = ["field_id"] + [v for v in rename.values() if v in set(df.columns) - {"field_id"}]
        available = [c for c in cols if c in df.columns]
        return df[available]
    return None


def load_crop_rotation(farm_dir: Path) -> pd.DataFrame | None:
    candidates = list((farm_dir / "derived" / "tables").glob("*crop_rotation*"))
    if candidates:
        return pd.read_csv(candidates[0])
    return None


def load_cdl_for_year(farm_dir: Path, field_ids: list[str], year: int) -> dict[str, str]:
    """Get dominant crop per field from CDL composition data."""
    result = {}
    candidates = list((farm_dir / "derived" / "tables").glob(f"*{year}_cdl*"))
    if not candidates:
        candidates = list((farm_dir / "derived" / "tables").glob(f"*full_composition*"))
    if candidates:
        cdl = pd.read_csv(candidates[0])
        cdl_year = cdl[cdl["year"] == year]
        for fid in field_ids:
            fd = cdl_year[cdl_year["field_id"] == fid]
            if not fd.empty:
                top = fd.loc[fd["pct"].idxmax()]
                result[fid] = top.get("crop_name", "")
    return result


def integrate_grower(runtime_dir: str, grower_id: str, year: int) -> pd.DataFrame:
    farms = discover_farms(runtime_dir, grower_id)
    all_fields = []

    for farm_dir in farms:
        farm_name = farm_dir.name
        boundaries = load_field_boundaries(farm_dir)
        field_ids = boundaries["field_id"].tolist()
        fields_dir = farm_dir / "fields"

        ndvi = load_ndvi_summary(fields_dir, field_ids, year)
        weather = load_weather(fields_dir, field_ids, year)
        soil = load_soil_summary(farm_dir)
        rotation = load_crop_rotation(farm_dir)
        cdl_crops = load_cdl_for_year(farm_dir, field_ids, year)

        for _, b in boundaries.iterrows():
            fid = b["field_id"]
            row = {
                "grower_id": grower_id,
                "farm_name": farm_name,
                "field_id": fid,
                "area_acres": round(b.get("area_acres", 0), 2),
                "centroid_lat": round(b.geometry.centroid.y, 6),
                "centroid_lon": round(b.geometry.centroid.x, 6),
            }
            crop_info = ndvi[ndvi["field_id"] == fid]
            if not crop_info.empty:
                ci = crop_info.iloc[0]
                crop_name = ci.get("crop_name", "")
                if not crop_name or pd.isna(crop_name):
                    crop_name = cdl_crops.get(fid, "")
                row["crop_name"] = crop_name
                row["scene_count"] = ci.get("scene_count", 0)
                for col in ["mean_ndvi", "peak_ndvi"]:
                    if col in ci and pd.notna(ci[col]):
                        row[col] = round(ci[col], 4)
            else:
                row["crop_name"] = cdl_crops.get(fid, "")

            w = weather[weather["field_id"] == fid] if not weather.empty else pd.DataFrame()
            if not w.empty:
                wi = w.iloc[0]
                for col in ["total_precipitation_mm", "mean_temp_c", "max_temp_c", "min_temp_c",
                            "cumulative_gdd", "dry_day_count"]:
                    if col in wi and pd.notna(wi[col]):
                        val = wi[col]
                        row[col] = round(val, 2) if isinstance(val, float) else val

            if soil is not None:
                s = soil[soil["field_id"] == fid]
                if not s.empty:
                    si = s.iloc[0]
                    for col in ["organic_matter_pct", "soil_ph", "available_water_capacity_in",
                                "cec_meq100g", "clay_pct", "sand_pct", "drainage_class",
                                "dominant_soil", "dominant_mapunit_name", "dominant_mapunit_pct",
                                "erosion_risk", "k_factor", "erosion_evidence_source",
                                "om_depth_cm", "ph_depth_cm", "cec_depth_cm", "awc_profile_depth_cm"]:
                        if col in si and pd.notna(si[col]):
                            row[col] = si[col]

            if rotation is not None:
                r = rotation[rotation["field_id"] == fid]
                if not r.empty:
                    ri = r.iloc[0]
                    for col in ["rotation_sequence", "predicted_next_crop", "rotation_outlook"]:
                        if col in ri and pd.notna(ri[col]):
                            row[col] = ri[col]

            all_fields.append(row)

    return pd.DataFrame(all_fields)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Integrate farm data into unified field-year summary")
    parser.add_argument("--grower-id", required=True)
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--year", type=int, default=2024)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    df = integrate_grower(args.runtime_dir, args.grower_id, args.year)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    print(f"Integrated {len(df)} fields -> {out_path}")


if __name__ == "__main__":
    sys.exit(main())
