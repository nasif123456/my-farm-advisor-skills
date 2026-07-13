#!/usr/bin/env python3
"""Validate input data quality before dashboard generation."""

from __future__ import annotations

import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

log = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    issues: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    passed: bool = True
    field_count: int = 0
    empty_fields: list[str] = field(default_factory=list)
    missing_data_fields: dict[str, list[str]] = field(default_factory=dict)


def validate_field_boundaries(path: Path) -> ValidationResult:
    result = ValidationResult()
    if not path.exists():
        result.issues.append({"severity": "error", "message": f"Boundary file not found: {path}"})
        result.passed = False
        return result
    gdf = gpd.read_file(path)
    if "field_id" not in gdf.columns:
        result.issues.append({"severity": "error", "message": "Missing field_id column in boundaries"})
        result.passed = False
    invalid_geom = gdf[~gdf.geometry.is_valid]
    for idx in invalid_geom.index:
        result.issues.append({
            "severity": "warning",
            "field_id": gdf.at[idx, "field_id"] if "field_id" in gdf.columns else str(idx),
            "message": "Invalid geometry"
        })
    dupes = gdf[gdf["field_id"].duplicated()] if "field_id" in gdf.columns else pd.DataFrame()
    for _, row in dupes.iterrows():
        result.issues.append({
            "severity": "error",
            "field_id": row["field_id"],
            "message": "Duplicate field_id in boundaries"
        })
        result.passed = False
    result.field_count = len(gdf)
    return result


def validate_weather_dir(weather_dir: Path, field_ids: list[str]) -> dict[str, ValidationResult]:
    results = {}
    for fid in field_ids:
        r = ValidationResult()
        csv_path = weather_dir / fid / "weather" / "daily_weather.csv"
        if not csv_path.exists():
            r.issues.append({"severity": "warning", "field_id": fid, "message": "Weather CSV not found"})
            r.warnings.append(f"Field {fid}: missing weather data")
            results[fid] = r
            continue
        try:
            df = pd.read_csv(csv_path)
            required = {"date", "T2M_MIN", "T2M_MAX", "PRECTOTCORR"}
            missing = required - set(df.columns)
            if missing:
                r.issues.append({
                    "severity": "error",
                    "field_id": fid,
                    "message": f"Missing weather columns: {missing}"
                })
                r.passed = False
            if df["PRECTOTCORR"].min() < -0.1:
                r.issues.append({"severity": "warning", "field_id": fid, "message": "Negative precipitation values"})
            temp_diff = df["T2M_MAX"] - df["T2M_MIN"]
            if (temp_diff < -0.1).any():
                r.issues.append({"severity": "warning", "field_id": fid, "message": "T2M_MAX < T2M_MIN in some rows"})
        except Exception as e:
            r.issues.append({"severity": "error", "field_id": fid, "message": f"Could not read weather CSV: {e}"})
            r.passed = False
        results[fid] = r
    return results


def validate_soil_summary(path: Path) -> ValidationResult:
    result = ValidationResult()
    if not path.exists():
        result.issues.append({"severity": "warning", "message": "Soil summary not found"})
        return result
    df = pd.read_csv(path)
    required = {"field_id", "avg_om_pct", "avg_ph", "drainage_class"}
    missing = required - set(df.columns)
    if missing:
        result.issues.append({"severity": "warning", "message": f"Missing soil columns: {missing}"})
    return result


def validate_ndvi_data(fields_dir: Path, field_ids: list[str]) -> dict[str, ValidationResult]:
    results = {}
    for fid in field_ids:
        r = ValidationResult()
        summary_path = fields_dir / fid / "derived" / "summaries" / "ndvi_card_summary.json"
        if not summary_path.exists():
            r.warnings.append(f"Field {fid}: no NDVI card summary")
            results[fid] = r
            continue
        try:
            with open(summary_path) as f:
                data = json.load(f)
            cards = data.get("cards", {})
            for crop in ["corn", "soybean"]:
                card = cards.get(crop, {})
                if card.get("status") == "available":
                    mn = card.get("mean_ndvi")
                    if mn is not None and (mn < -1 or mn > 1):
                        r.issues.append({
                            "severity": "warning",
                            "field_id": fid,
                            "message": f"NDVI out of range for {crop}: {mn}"
                        })
        except Exception as e:
            r.issues.append({"severity": "warning", "field_id": fid, "message": f"NDVI read error: {e}"})
        results[fid] = r
    return results


def run_validation(runtime_dir: str, grower_id: str, year: int) -> dict[str, Any]:
    from pathlib import Path
    rt = Path(runtime_dir) / "data-pipeline" / "growers" / grower_id / "farms"
    farms = list(rt.iterdir()) if rt.exists() else []

    report = {
        "grower": grower_id,
        "year": year,
        "farms": [],
        "overall_pass": True,
        "data_quality_notes": []
    }

    for farm_dir in farms:
        farm_name = farm_dir.name
        boundary_path = farm_dir / "boundary" / "field_boundaries.geojson"
        soil_path = farm_dir / "derived" / "tables" / f"{grower_id}_{farm_name}_ssurgo_summary.csv"

        bv = validate_field_boundaries(boundary_path)
        sv = validate_soil_summary(soil_path)

        field_ids = []
        if bv.field_count > 0:
            gdf = gpd.read_file(boundary_path)
            field_ids = gdf["field_id"].tolist()

        wv = validate_weather_dir(farm_dir / "fields", field_ids) if field_ids else {}
        nv = validate_ndvi_data(farm_dir / "fields", field_ids) if field_ids else {}

        farm_entry = {
            "farm": farm_name,
            "field_count": bv.field_count,
            "boundary_valid": bv.passed,
            "boundary_issues": len(bv.issues),
            "soil_available": sv.passed,
            "fields_with_weather": sum(1 for v in wv.values() if v.passed),
            "fields_with_ndvi": sum(1 for v in nv.values() if len(v.issues) == 0),
            "issues": bv.issues,
            "warnings": bv.warnings,
        }
        report["farms"].append(farm_entry)

        if not bv.passed or not sv.passed:
            report["overall_pass"] = False

    return report


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Validate input data for dashboard generation")
    parser.add_argument("--grower-id", required=True)
    parser.add_argument("--runtime-dir", required=True)
    parser.add_argument("--year", type=int, default=2024)
    args = parser.parse_args()

    report = run_validation(args.runtime_dir, args.grower_id, args.year)
    print(json.dumps(report, indent=2))
    return 0 if report["overall_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
