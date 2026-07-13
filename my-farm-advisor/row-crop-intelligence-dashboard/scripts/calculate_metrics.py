#!/usr/bin/env python3
"""Compute composite metrics from integrated field data."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.metrics import compute_all_metrics

log = logging.getLogger(__name__)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Calculate composite field metrics")
    parser.add_argument("--input", required=True, help="Integrated field summary CSV")
    parser.add_argument("--output", required=True, help="Output CSV with metrics")
    args = parser.parse_args()

    df = pd.read_csv(args.input)
    result = compute_all_metrics(df)
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    result.to_csv(out_path, index=False)
    print(f"Metrics computed for {len(result)} fields -> {out_path}")

    score_cols = [c for c in result.columns if c.endswith("_score") or c in (
        "field_intelligence_score", "crop_stress_indicator",
        "conservation_priority_score", "soil_health_score",
        "weather_suitability_score", "ndvi_score", "ndvi_variability_score"
    )]
    available = [c for c in score_cols if c in result.columns]
    if available:
        print("\nScore ranges:")
        print(result[available].describe().loc[["min", "max", "mean"]].to_string())


if __name__ == "__main__":
    sys.exit(main())
