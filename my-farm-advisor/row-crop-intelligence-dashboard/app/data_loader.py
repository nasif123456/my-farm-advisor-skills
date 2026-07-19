#!/usr/bin/env python3
"""Load and provide dashboard input data from prepared files."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd

log = logging.getLogger(__name__)


@dataclass
class DashboardData:
    field_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    field_boundaries: gpd.GeoDataFrame = field(default_factory=gpd.GeoDataFrame)
    ndvi_timeseries: pd.DataFrame = field(default_factory=pd.DataFrame)
    weather_timeseries: pd.DataFrame = field(default_factory=pd.DataFrame)
    soil_summary: pd.DataFrame = field(default_factory=pd.DataFrame)
    interpretations: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


def load_dashboard_data(data_dir: str) -> DashboardData:
    d = DashboardData()
    base = Path(data_dir)

    summary_path = base / "field_summary.csv"
    if summary_path.exists():
        d.field_summary = pd.read_csv(summary_path)

    boundaries_path = base / "field_boundaries.geojson"
    if boundaries_path.exists():
        d.field_boundaries = gpd.read_file(boundaries_path)

    ndvi_path = base / "ndvi_timeseries.csv"
    if ndvi_path.exists():
        d.ndvi_timeseries = pd.read_csv(ndvi_path)

    weather_path = base / "weather_timeseries.csv"
    if weather_path.exists():
        d.weather_timeseries = pd.read_csv(weather_path)

    soil_path = base / "soil_summary.csv"
    if soil_path.exists():
        d.soil_summary = pd.read_csv(soil_path)

    interpretations_path = base / "interpretations.json"
    if interpretations_path.exists():
        with open(interpretations_path) as f:
            d.interpretations = json.load(f)

    metadata_path = base / "dashboard_metadata.json"
    if metadata_path.exists():
        with open(metadata_path) as f:
            d.metadata = json.load(f)

    return d


def validate_dashboard_data(d: DashboardData) -> list[str]:
    warnings = []
    if d.field_summary.empty:
        warnings.append("Field summary is empty. No fields to display.")
    if d.field_boundaries.empty:
        warnings.append("Field boundaries are missing. Map will not render.")
    if d.field_summary is not None and not d.field_summary.empty:
        na_counts = d.field_summary.isna().sum()
        high_na = na_counts[na_counts > len(d.field_summary) * 0.5]
        for col in high_na.index:
            warnings.append(f"Column '{col}' has >50% missing values.")
    return warnings
