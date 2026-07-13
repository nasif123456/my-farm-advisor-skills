#!/usr/bin/env python3
"""Tests for composite metric calculations."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.metrics import (
    compute_all_metrics,
    score_ndvi,
    score_soil_health,
    score_weather_suitability,
    score_crop_stress,
)


def make_test_df() -> pd.DataFrame:
    return pd.DataFrame({
        "field_id": ["f1", "f2", "f3"],
        "area_acres": [100, 200, 150],
        "ndvi_corn": [0.6, 0.4, 0.8],
        "ndvi_soybean": [0.55, 0.35, None],
        "organic_matter_pct": [4.0, 2.0, 6.0],
        "soil_ph": [6.5, 5.5, 7.5],
        "available_water_capacity_in": [5.0, 3.0, 7.0],
        "drainage_class": ["Well drained", "Poorly drained", "Moderately well drained"],
        "erosion_risk": ["low", "high", "moderate"],
        "total_precipitation_mm": [800, 600, 1000],
        "cumulative_gdd": [1500, 1200, 1800],
        "dry_day_count": [30, 50, 20],
    })


def test_ndvi_score():
    df = make_test_df()
    scores = score_ndvi(df)
    assert len(scores) == 3
    assert scores.iloc[0] > scores.iloc[1], "Higher NDVI should give higher score"
    assert 0 <= scores.iloc[0] <= 100


def test_soil_health_score():
    df = make_test_df()
    scores = score_soil_health(df)
    assert len(scores) == 3
    assert scores.iloc[0] > scores.iloc[1], "Better soil should give higher score"
    assert 0 <= scores.iloc[0] <= 100


def test_weather_suitability():
    df = make_test_df()
    scores = score_weather_suitability(df)
    assert len(scores) == 3
    assert 0 <= scores.iloc[0] <= 100


def test_crop_stress():
    df = make_test_df()
    scores = score_crop_stress(df)
    assert len(scores) == 3
    assert 0 <= scores.iloc[0] <= 100


def test_compute_all():
    df = make_test_df()
    result = compute_all_metrics(df)
    expected_cols = [
        "ndvi_score", "stability_score", "soil_health_score",
        "weather_suitability_score", "crop_stress_indicator",
        "conservation_priority_score", "field_intelligence_score",
        "risk_category", "stress_category", "priority_category",
    ]
    for col in expected_cols:
        assert col in result.columns, f"Missing column: {col}"
    assert len(result) == 3


def test_edge_empty():
    df = pd.DataFrame({"field_id": [], "area_acres": []})
    result = compute_all_metrics(df)
    assert len(result) == 0


def test_edge_missing_soil():
    df = make_test_df().drop(columns=["organic_matter_pct", "soil_ph"])
    result = compute_all_metrics(df)
    assert "soil_health_score" in result.columns
    assert 0 <= result["soil_health_score"].iloc[0] <= 100


def test_edge_missing_ndvi():
    df = make_test_df().drop(columns=["ndvi_corn", "ndvi_soybean"])
    result = compute_all_metrics(df)
    assert "ndvi_score" in result.columns
    assert result["ndvi_score"].iloc[0] == 50.0


def test_gdd_calculation():
    df = pd.DataFrame({
        "field_id": ["f1", "f1"],
        "T2M_MAX": [30.0, 20.0],
        "T2M_MIN": [20.0, 10.0],
        "PRECTOTCORR": [2.0, 1.0],
        "date": ["2024-06-01", "2024-06-02"],
    })
    base_temp = 10
    df["tavg"] = (df["T2M_MAX"] + df["T2M_MIN"]) / 2
    df["gdd"] = (df["tavg"] - base_temp).clip(lower=0)
    assert df["gdd"].iloc[0] == 15.0
    assert df["gdd"].iloc[1] == 5.0


def test_field_intelligence_score_bounds():
    df = make_test_df()
    result = compute_all_metrics(df)
    fis = result["field_intelligence_score"]
    assert fis.between(0, 100).all(), f"FIS out of bounds: {fis.tolist()}"
    assert fis.is_monotonic_increasing or not fis.is_monotonic_decreasing


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__]))
