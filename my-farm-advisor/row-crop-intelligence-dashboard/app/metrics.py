#!/usr/bin/env python3
"""Composite metric calculations for the row crop intelligence dashboard."""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def _normalize(values: pd.Series, lower_better: bool = False) -> pd.Series:
    """Normalize a series to 0-100 scale."""
    if values.empty or values.isna().all():
        return pd.Series(50.0, index=values.index)
    vmin, vmax = values.min(), values.max()
    if vmax == vmin:
        return pd.Series(50.0, index=values.index)
    norm = (values - vmin) / (vmax - vmin) * 100
    if lower_better:
        norm = 100 - norm
    return norm


def score_ndvi(df: pd.DataFrame) -> pd.Series:
    """Crop Health component from NDVI values.
    
    Maps NDVI (0-1) to a 0-100 score. NDVI of 0.2 → ~20, 0.5 → ~60, 0.8 → ~90.
    Typical agricultural NDVI ranges: 0.2-0.8 for row crops.
    """
    ndvi_cols = [c for c in ["ndvi_corn", "ndvi_soybean"] if c in df.columns]
    if not ndvi_cols:
        return pd.Series(50.0, index=df.index)
    combined = df[ndvi_cols].mean(axis=1, skipna=True)
    combined = combined.fillna(combined.median() if not combined.isna().all() else 50.0)
    score = ((combined - 0.1) / 0.7 * 100).clip(0, 100)
    return score.fillna(50.0)


def score_stability(df: pd.DataFrame) -> pd.Series:
    """Stability score from NDVI variability and peak performance.
    
    Uses available NDVI data. Higher peak NDVI relative to mean suggests
    good growing conditions; lower variability is more stable.
    Higher = more stable.
    """
    has_direct = False
    peak_cols = [c for c in ["ndvi_corn_peak_95", "ndvi_soybean_peak_95"] if c in df.columns]
    ndvi_mean_cols = [c for c in ["ndvi_corn", "ndvi_soybean"] if c in df.columns]

    if peak_cols and ndvi_mean_cols:
        peaks = df[peak_cols].mean(axis=1, skipna=True)
        means = df[ndvi_mean_cols].mean(axis=1, skipna=True)
        ratio = (peaks / (means + 0.01)).clip(1, 3) - 1
        score = (1 - ratio / 2) * 100
        has_direct = True

    if ndvi_mean_cols and not has_direct:
        means = df[ndvi_mean_cols].mean(axis=1, skipna=True)
        score = ((means - 0.1) / 0.7 * 100).clip(0, 100)
        has_direct = True

    if not has_direct:
        return pd.Series(60.0, index=df.index)

    return score.fillna(60.0).clip(0, 100)


def score_soil_health(df: pd.DataFrame, weights: dict[str, float] | None = None) -> pd.Series:
    """Soil Health Score (0-100) from available soil properties."""
    if weights is None:
        weights = {"organic_matter": 0.25, "ph": 0.20, "awc": 0.20, "drainage": 0.20, "slope": 0.15}

    components = []

    if "organic_matter_pct" in df.columns:
        om = df["organic_matter_pct"].fillna(df["organic_matter_pct"].median())
        om_score = om.clip(0, 10) / 10 * 100
        components.append(om_score * weights["organic_matter"])

    if "soil_ph" in df.columns:
        ph = df["soil_ph"].fillna(df["soil_ph"].median())
        ph_score = 100 - (ph - 6.5).abs() * 40
        ph_score = ph_score.clip(0, 100)
        components.append(ph_score * weights["ph"])

    if "available_water_capacity_in" in df.columns:
        awc = df["available_water_capacity_in"].fillna(df["available_water_capacity_in"].median())
        awc_score = awc.clip(0, 10) / 10 * 100
        components.append(awc_score * weights["awc"])

    if "drainage_class" in df.columns:
        drainage_map = {
            "Excessively drained": 40, "Somewhat excessively drained": 50,
            "Well drained": 90, "Moderately well drained": 75,
            "Somewhat poorly drained": 55, "Poorly drained": 40,
            "Very poorly drained": 30,
        }
        dr = df["drainage_class"].fillna("Poorly drained").map(drainage_map).fillna(50)
        components.append(dr * weights["drainage"])

    if not components:
        return pd.Series(50.0, index=df.index)

    total = sum(components)
    weight_sum = sum(weights.get(k, 0) for k in ["organic_matter", "ph", "awc", "drainage", "slope"]
                     if any(c.startswith(k[0]) for c in df.columns))
    weight_sum = weight_sum if weight_sum > 0 else 1.0
    return (total / weight_sum).fillna(50.0).clip(0, 100)


def score_weather_suitability(df: pd.DataFrame) -> pd.Series:
    """Weather Suitability Score (0-100) from weather variables."""
    scores = []

    if "total_precipitation_mm" in df.columns:
        precip = df["total_precipitation_mm"].fillna(df["total_precipitation_mm"].median())
        scores.append(_normalize(precip))

    if "cumulative_gdd" in df.columns:
        gdd = df["cumulative_gdd"].fillna(df["cumulative_gdd"].median())
        scores.append(_normalize(gdd))

    if "dry_day_count" in df.columns:
        dry = df["dry_day_count"].fillna(df["dry_day_count"].median())
        scores.append(_normalize(dry, lower_better=True))

    if not scores:
        return pd.Series(50.0, index=df.index)

    return pd.concat(scores, axis=1).mean(axis=1).fillna(50.0).clip(0, 100)


def score_crop_stress(df: pd.DataFrame) -> pd.Series:
    """Crop Stress Indicator (0-100). Higher = more apparent stress.
    
    0.5 × Low NDVI + 0.3 × Instability + 0.2 × Early Decline (approximated).
    """
    low_ndvi = 100 - score_ndvi(df)
    instability = 100 - score_stability(df)
    early_decline = low_ndvi
    return (0.5 * low_ndvi + 0.3 * instability + 0.2 * early_decline).fillna(50.0).clip(0, 100)


def score_conservation_priority(df: pd.DataFrame) -> pd.Series:
    """Conservation Priority Score (0-100). Higher = greater need for attention."""
    soil_limitation = 100 - score_soil_health(df)
    crop_stress = score_crop_stress(df)
    erosion = 0.0
    if "erosion_risk" in df.columns:
        erosion_map = {"low": 0, "moderate": 50, "high": 100, "severe": 100}
        er = df["erosion_risk"].fillna("moderate").map(erosion_map).fillna(50)
        erosion = er
    weather_exposure = 100 - score_weather_suitability(df)
    return (0.35 * soil_limitation + 0.30 * crop_stress + 0.20 * erosion + 0.15 * weather_exposure).fillna(50.0).clip(0, 100)


def compute_all_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all composite metrics and add them to the DataFrame."""
    result = df.copy()
    result["ndvi_score"] = score_ndvi(df).round(1)
    result["stability_score"] = score_stability(df).round(1)
    result["soil_health_score"] = score_soil_health(df).round(1)
    result["weather_suitability_score"] = score_weather_suitability(df).round(1)
    result["crop_stress_indicator"] = score_crop_stress(df).round(1)
    result["conservation_priority_score"] = score_conservation_priority(df).round(1)

    field_intelligence = (
        0.40 * result["ndvi_score"]
        + 0.30 * result["soil_health_score"]
        + 0.20 * result["weather_suitability_score"]
        + 0.10 * result["stability_score"]
    )
    result["field_intelligence_score"] = field_intelligence.round(1).clip(0, 100)

    def risk_class(score):
        if score >= 75:
            return "Low"
        elif score >= 60:
            return "Moderate"
        elif score >= 45:
            return "Elevated"
        return "High"

    def stress_class(val):
        if val <= 35:
            return "Low"
        elif val <= 65:
            return "Moderate"
        return "High"

    def priority_class(val):
        if val <= 35:
            return "Routine"
        elif val <= 65:
            return "Monitor"
        return "Priority"

    result["risk_category"] = result["field_intelligence_score"].apply(risk_class)
    result["stress_category"] = result["crop_stress_indicator"].apply(stress_class)
    result["priority_category"] = result["conservation_priority_score"].apply(priority_class)

    return result
