#!/usr/bin/env python3
"""Composite metric calculations for the row crop intelligence dashboard.

All composite scores are 0-100. Raw biophysical measurements (e.g. mean_ndvi)
are stored alongside scores so users can inspect both the original data and
the transformed metric.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml


# ──────────────────────────────────────────────
# Config file loader
# ──────────────────────────────────────────────

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _load_yaml(name: str) -> dict | None:
    """Load a YAML config file from the config directory.
    Returns None if the file is missing or unreadable.
    """
    path = _CONFIG_DIR / name
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except Exception:
        return None


def _load_metric_weights() -> dict[str, dict[str, float]]:
    """Load metric weights from config/metric_weights.yaml.
    Falls back to module-level defaults if not available.
    """
    raw = _load_yaml("metric_weights.yaml")
    if raw is None:
        return {}
    return {
        "fis": raw.get("field_intelligence_score", {}),
        "crop_stress": raw.get("crop_stress_indicator", {}),
        "conservation": raw.get("conservation_priority", {}),
        "soil_condition": raw.get("soil_condition_screening_score", {}),
        "soil_screening_categories": raw.get("soil_screening_categories", {}),
    }


# Attempt to load weights from config; fall back to constants below
_CONFIG_WEIGHTS = _load_metric_weights()

# ──────────────────────────────────────────────
# Configuration — metric weights and thresholds
# ──────────────────────────────────────────────
# Defaults are used when config files are missing.
# When config/metric_weights.yaml is present, those values override these defaults.
# Weights are stored as module-level constants, not embedded in formulas.

FIS_WEIGHTS = _CONFIG_WEIGHTS.get("fis", {}) or {
    "crop_health": 0.45,
    "soil_condition": 0.25,
    "weather_suitability": 0.20,
    "stability": 0.10,
}

CROP_STRESS_WEIGHTS = _CONFIG_WEIGHTS.get("crop_stress", {}) or {
    "low_ndvi": 0.45,
    "ndvi_decline": 0.25,
    "variability": 0.20,
    "weather_stress": 0.10,
}

CONSERVATION_PRIORITY_WEIGHTS = _CONFIG_WEIGHTS.get("conservation", {}) or {
    "erosion_risk": 0.30,
    "drainage_concern": 0.30,
    "soil_condition_screening": 0.25,
    "sand_content": 0.15,
}

SOIL_CONDITION_WEIGHTS = _CONFIG_WEIGHTS.get("soil_condition", {}) or {
    "organic_matter": 0.30,
    "ph_suitability": 0.20,
    "available_water_capacity": 0.20,
    "drainage": 0.20,
    "cec": 0.10,
}

SOIL_SCREENING_CATEGORIES: dict[str, float] = _CONFIG_WEIGHTS.get("soil_screening_categories", {}) or {
    "Strong screening condition": 80,
    "Moderately strong screening condition": 65,
    "Moderate screening condition": 50,
    "Constrained screening condition": 35,
    "Poor screening condition": 0,
}

# Growing season defaults (overridden by crop_parameters.yaml when available)
GDD_BASE_TEMP_C = 10
GDD_UPPER_CAP_C = 30

# Load crop-specific GDD parameters from config
_CROP_PARAMS = _load_yaml("crop_parameters.yaml")
_CROP_GDD_BASES: dict[str, float] = {}
if _CROP_PARAMS:
    for crop_name, params in _CROP_PARAMS.get("crop_parameters", {}).items():
        base = params.get("gdd_base_temp_c")
        if base is not None:
            _CROP_GDD_BASES[crop_name.lower()] = float(base)


def _get_gdd_base(crop_name: str) -> float:
    """Return GDD base temperature for a crop type (default 10°C)."""
    key = str(crop_name).strip().lower()
    return _CROP_GDD_BASES.get(key, GDD_BASE_TEMP_C)

# NDVI reference thresholds for fixed-range scoring
ROW_CROP_NDVI_LOW = 0.15
ROW_CROP_NDVI_HIGH = 0.85

# Weather thresholds
DRY_DAY_THRESHOLD_MM = 1.0
EXTREME_HEAT_THRESHOLD_C = 35
GDD_CAP = 4000
PRECIP_CAP_MM = 1200

# Confidence categories
CONFIDENCE_HIGH = "High"
CONFIDENCE_MODERATE = "Moderate"
CONFIDENCE_LOW = "Low"
CONFIDENCE_INSUFFICIENT = "Insufficient data"


# ──────────────────────────────────────────────
# Scoring helpers
# ──────────────────────────────────────────────


def _normalize(values: pd.Series, lower_better: bool = False) -> pd.Series:
    """Min-max normalise to 0-100 across the dataset.

    When all values are identical (or all missing), returns 50 for each row
    rather than failing.  This is a relative ranking within the current group
    of fields, not an absolute scale.
    """
    if values.empty or values.isna().all():
        return pd.Series(50.0, index=values.index)
    vmin, vmax = values.min(), values.max()
    if vmax == vmin:
        return pd.Series(50.0, index=values.index)
    norm = (values - vmin) / (vmax - vmin) * 100
    if lower_better:
        norm = 100 - norm
    return norm


def _min_max_scale(series: pd.Series, lo: float, hi: float, clip: bool = True) -> pd.Series:
    """Scale a series so that value `lo` maps to 0 and `hi` maps to 100.

    This is an absolute (not relative) transform — suitable for mapping NDVI
    to a 0-100 condition score using crop-specific reference ranges.
    """
    denom = hi - lo if hi != lo else 1.0
    result = (series - lo) / denom * 100.0
    if clip:
        result = result.clip(0, 100)
    return result


# ──────────────────────────────────────────────
# NDVI raw-data extraction helpers (biophysical, not scores)
# ──────────────────────────────────────────────


def extract_ndvi_stats(df: pd.DataFrame) -> pd.DataFrame:
    """Extract raw NDVI statistics from input columns.

    Adds columns for mean_ndvi, peak_ndvi, ndvi_std, ndvi_cv, and
    valid_ndvi_observations.  These are the actual biophysical measurements,
    not 0-100 scores.

    The input should have crop-neutral NDVI columns.  When the data contains
    crop-specific columns (ndvi_corn, ndvi_soybean) instead, they are merged
    by taking the mean across available crops per field.
    """
    result = df.copy()

    # Detect crop-neutral vs crop-specific NDVI columns
    neutral_cols = [c for c in ["mean_ndvi", "ndvi"] if c in df.columns]
    corn_cols = [c for c in ["ndvi_corn"] if c in df.columns]
    soy_cols = [c for c in ["ndvi_soybean"] if c in df.columns]
    peak_corn = [c for c in ["ndvi_corn_peak_95"] if c in df.columns]
    peak_soy = [c for c in ["ndvi_soybean_peak_95"] if c in df.columns]

    # Mean NDVI
    if neutral_cols:
        result["mean_ndvi"] = df[neutral_cols].mean(axis=1, skipna=True)
    elif corn_cols or soy_cols:
        cols = corn_cols + soy_cols
        result["mean_ndvi"] = df[cols].mean(axis=1, skipna=True)
    else:
        result["mean_ndvi"] = float("nan")

    # Peak NDVI
    peak_cols = peak_corn + peak_soy
    if peak_cols:
        result["peak_ndvi"] = df[peak_cols].max(axis=1, skipna=True)
    else:
        result["peak_ndvi"] = float("nan")

    # NDVI standard deviation and coefficient of variation
    ndvi_cols = [c for c in ["ndvi_corn", "ndvi_soybean"] if c in df.columns] + neutral_cols
    if ndvi_cols and len(ndvi_cols) > 1:
        stacked = df[ndvi_cols]
        n_valid = stacked.notna().sum(axis=1)
        result["ndvi_std"] = stacked.std(axis=1, skipna=True)
        result["ndvi_std"] = result["ndvi_std"].where(n_valid > 1, 0.0)
    elif ndvi_cols and len(ndvi_cols) == 1:
        result["ndvi_std"] = pd.Series(0.0, index=result.index)
    else:
        result["ndvi_std"] = float("nan")
    safe_mean = result["mean_ndvi"].replace(0, float("nan"))
    result["ndvi_cv"] = result["ndvi_std"] / safe_mean
    result["ndvi_cv"] = result["ndvi_cv"].fillna(0.0)

    # Valid observations
    scene = df.get("scene_count")
    if isinstance(scene, pd.Series):
        result["valid_ndvi_observations"] = scene.fillna(0).astype(int)
    else:
        result["valid_ndvi_observations"] = 0

    # Crop name fallback
    if "crop_name" not in result.columns:
        result["crop_name"] = "Unknown"
    result["crop_name"] = result["crop_name"].fillna("Unknown")

    return result


def _is_row_crop(crop_name: str) -> bool:
    """Return True if the crop is a supported row crop for primary scoring."""
    return str(crop_name).strip().lower() in (
        "corn", "soybeans", "soybean", "wheat", "cotton", "rice", "sorghum"
    )


# ──────────────────────────────────────────────
# Per-crop NDVI reference bounds for fixed-range scoring
# ──────────────────────────────────────────────

_CROP_NDVI_RANGES: dict[str, tuple[float, float]] = {
    "corn": (0.15, 0.85),
    "soybeans": (0.15, 0.85),
    "soybean": (0.15, 0.85),
    "wheat": (0.15, 0.80),
    "cotton": (0.15, 0.75),
    "rice": (0.15, 0.75),
    "sorghum": (0.15, 0.80),
}


def _get_ndvi_range(crop_name: str) -> tuple[float, float]:
    """Return (low, high) NDVI bounds for a crop type.

    Row crops use standardised bounds.  Non-row-crop and unknown types
    use wider bounds (0.0-1.0), which flags lower analytical confidence
    for the resulting condition score.
    """
    key = str(crop_name).strip().lower()
    if key in _CROP_NDVI_RANGES:
        return _CROP_NDVI_RANGES[key]
    return (0.0, 1.0)


# ──────────────────────────────────────────────
# Weather helpers
# ──────────────────────────────────────────────


def _estimate_gdd(tmax: float, tmin: float, base: float = GDD_BASE_TEMP_C,
                  upper: float | None = GDD_UPPER_CAP_C) -> float:
    """Estimate daily growing degree days using the average method."""
    if pd.isna(tmax) or pd.isna(tmin):
        return float("nan")
    avg = (tmax + tmin) / 2.0
    if upper is not None:
        avg = min(avg, upper)
    return max(0.0, avg - base)


def assign_weather_grid_ids(df: pd.DataFrame,
                            lat_col: str = "centroid_lat",
                            lon_col: str = "centroid_lon",
                            precision: int = 2) -> pd.Series:
    """Assign a weather-grid identifier to each field by rounding centroid
    coordinates to `precision` decimal places (~11 km at mid-latitudes for
    precision=2).  Fields sharing the same grid ID likely come from the same
    NASA POWER cell and should not be treated as having independent weather.
    """
    lat_r = df[lat_col].round(precision)
    lon_r = df[lon_col].round(precision)
    return "W" + lat_r.astype(str) + "_" + lon_r.astype(str)


# ──────────────────────────────────────────────
# NDVI Condition Score (0-100) — crop-aware
# ──────────────────────────────────────────────


def score_ndvi_condition(df: pd.DataFrame) -> pd.DataFrame:
    """Compute NDVI Condition Score per field, normalised within crop type
    where enough comparable fields exist.

    Output columns added:
      ndvi_condition_score  — 0-100 transformed crop-condition score
      ndvi_condition_method  — description of scoring approach
      ndvi_condition_confidence — High / Moderate / Low / Insufficient data

    For row-crop fields with at least 3 same-crop peers, normalisation is
    performed within crop group.  Otherwise a fixed reference-range transform
    is used via _get_ndvi_range().
    """
    result = df.copy()

    if "mean_ndvi" not in result.columns:
        result = extract_ndvi_stats(result)

    result["ndvi_condition_score"] = float("nan")
    result["ndvi_condition_method"] = ""
    result["ndvi_condition_confidence"] = CONFIDENCE_INSUFFICIENT

    crop_groups = result.groupby("crop_name")["mean_ndvi"]
    crop_sizes = crop_groups.count()

    for idx in result.index:
        row = result.loc[idx]
        crop = row.get("crop_name", "Unknown")
        mean_ndvi = row.get("mean_ndvi")
        if mean_ndvi is None or (isinstance(mean_ndvi, float) and pd.isna(mean_ndvi)):
            continue

        lo, hi = _get_ndvi_range(crop)
        n_same_crop = crop_sizes.get(crop, 0)

        # Within-crop normalisation when enough peers
        if n_same_crop >= 3:
            group = crop_groups.get_group(crop).dropna()
            if len(group) >= 3:
                score = _normalize(group).loc[idx]
                result.at[idx, "ndvi_condition_score"] = score
                result.at[idx, "ndvi_condition_method"] = (
                    f"within-crop normalised ({crop}, n={n_same_crop})"
                )
                result.at[idx, "ndvi_condition_confidence"] = CONFIDENCE_HIGH
                continue

        # Fixed reference-range transform (fallback)
        score = _min_max_scale(pd.Series(mean_ndvi), lo, hi).iloc[0]
        result.at[idx, "ndvi_condition_score"] = score
        if _is_row_crop(crop):
            method = (
                f"fixed-range transform ({lo}-{hi}) — "
                f"too few {crop} peers for within-crop normalisation"
            )
            conf = CONFIDENCE_LOW
        else:
            method = f"wide-range transform ({lo}-{hi}) — non-row-crop"
            conf = CONFIDENCE_LOW
        result.at[idx, "ndvi_condition_method"] = method
        result.at[idx, "ndvi_condition_confidence"] = conf

    return result


# ──────────────────────────────────────────────
# Crop Health Score (0-100) — alias
# ──────────────────────────────────────────────


def score_crop_health(df: pd.DataFrame) -> pd.Series:
    """Crop Health Score = NDVI Condition Score.

    This is the primary greenness-based indicator.  Exposed as a named
    alias so the FIS formula is transparent.
    """
    if "ndvi_condition_score" not in df.columns:
        scored = score_ndvi_condition(df)
        return scored["ndvi_condition_score"]
    return df["ndvi_condition_score"].fillna(50.0)


# ──────────────────────────────────────────────
# Stability Score (0-100) — independent of Condition Score
# ──────────────────────────────────────────────


def score_stability(df: pd.DataFrame) -> pd.Series:
    """Stability Score derived from NDVI temporal variability.

    Higher values = more stable seasonal vegetation behaviour.

    Formula:
      Stability = 100 - normalised(ndvi_cv)

    Requires ndvi_cv (coefficient of variation).  When insufficient
    observations exist (<3 valid scenes), the score defaults to 50 with
    a low-confidence flag.

    This score is INDEPENDENT of the NDVI Condition Score — a field with
    consistently moderate NDVI can have high stability.
    """
    if "ndvi_cv" not in df.columns:
        stats = extract_ndvi_stats(df)
        ndvi_cv = stats["ndvi_cv"]
    else:
        ndvi_cv = df["ndvi_cv"]

    obs = df.get("valid_ndvi_observations", pd.Series(0, index=df.index))

    result = pd.Series(50.0, index=df.index)

    valid = ndvi_cv.notna() & (ndvi_cv >= 0) & (obs >= 3)
    if valid.any():
        cv_valid = ndvi_cv[valid]
        if cv_valid.nunique() > 1:
            stability_raw = _normalize(cv_valid, lower_better=True)
        else:
            stability_raw = pd.Series(50.0, index=cv_valid.index)
        result[valid] = stability_raw.values

    return result.clip(0, 100)


# ──────────────────────────────────────────────
# Weather Suitability Score (0-100)
# ──────────────────────────────────────────────


def score_weather(df: pd.DataFrame) -> pd.Series:
    """Weather Suitability Score from growing-season measurements.

    Components (dataset-normalised, then weighted):
      - GDD adequacy (40%): cumulative GDD, capped at GDD_CAP
      - Precipitation adequacy (30%): total precip, capped at PRECIP_CAP_MM
      - Dry-spell stress (15%): inverted consecutive dry days
      - Extreme-heat exposure (15%): inverted days above 35°C

    All components are normalised within the current field group, so the
    score is comparative (relative ranking) rather than absolute.
    """
    result = pd.Series(50.0, index=df.index)
    n_components = 0

    gdd = df.get("cumulative_gdd")
    if gdd is not None and not gdd.isna().all():
        result += _normalize(gdd.clip(upper=GDD_CAP)) * 0.40
        n_components += 1

    precip = df.get("total_precipitation_mm")
    if precip is not None and not precip.isna().all():
        result += _normalize(precip.clip(upper=PRECIP_CAP_MM)) * 0.30
        n_components += 1

    dry = df.get("dry_day_count")
    if dry is not None and not dry.isna().all():
        result += _normalize(dry, lower_better=True) * 0.15
        n_components += 1

    max_temp = df.get("max_temp_c")
    if max_temp is not None and not max_temp.isna().all():
        heat_stress = max_temp.apply(
            lambda x: max(0, min(100, (x - EXTREME_HEAT_THRESHOLD_C) / 10.0 * 100))
        )
        result += (100 - heat_stress) * 0.15
        n_components += 1

    return result.clip(0, 100)


# ──────────────────────────────────────────────
# Crop Stress Indicator (0-100) — genuine multi-component
# ──────────────────────────────────────────────


def score_crop_stress(df: pd.DataFrame) -> pd.DataFrame:
    """Apparent Crop Stress Indicator — a multi-component measure.

    Components (weights from CROP_STRESS_WEIGHTS):
      1. low_ndvi (45%): 100 - ndvi_condition_score
         → low crop-normalised NDVI drives stress.
      2. ndvi_decline (25%): derived from peak/mean ratio.  Higher
         ratio indicates more decline from peak.
      3. variability (20%): ndvi_cv normalised (higher CV => more stress).
      4. weather_stress (10%): 100 - weather_suitability_score, or a
         fallback from dry-day / heat components.

    Returns a DataFrame with added column `crop_stress_apparent` (0-100)
    and `crop_stress_confidence`.

    Uses the term 'apparent stress' because remote sensing cannot diagnose
    the exact cause of observed patterns.
    """
    result = df.copy()
    result["crop_stress_apparent"] = float("nan")
    result["crop_stress_confidence"] = CONFIDENCE_INSUFFICIENT

    # Ensure ndvi_condition_score is available
    if "ndvi_condition_score" not in result.columns:
        result = score_ndvi_condition(result)

    stress = pd.Series(0.0, index=result.index)

    # 1. Low NDVI component
    ndvi_score = result.get("ndvi_condition_score", pd.Series(50.0, index=result.index))
    stress += (100 - ndvi_score.fillna(50.0)) * CROP_STRESS_WEIGHTS["low_ndvi"]

    # 2. NDVI decline component (peak/mean ratio inverted)
    peak = result.get("peak_ndvi", pd.Series(float("nan"), index=result.index))
    mean = result.get("mean_ndvi", pd.Series(float("nan"), index=result.index))
    ratio = peak / mean.replace(0, float("nan"))
    if ratio.notna().any():
        # Higher peak/mean ratio → more decline stress
        decline = _normalize(ratio.fillna(1.0), lower_better=False)
        stress += decline * CROP_STRESS_WEIGHTS["ndvi_decline"]
    else:
        stress += 50.0 * CROP_STRESS_WEIGHTS["ndvi_decline"]

    # 3. Variability component (CV)
    cv = result.get("ndvi_cv", pd.Series(float("nan"), index=result.index))
    if cv.notna().any() and (cv > 0).any():
        var_stress = _normalize(cv, lower_better=False)
        stress += var_stress * CROP_STRESS_WEIGHTS["variability"]
    else:
        stress += 50.0 * CROP_STRESS_WEIGHTS["variability"]

    # 4. Weather stress component
    weather = result.get("weather_suitability_score",
                         pd.Series(float("nan"), index=result.index))
    if weather.notna().any():
        weather_stress = 100 - weather.fillna(50.0)
    else:
        dry = result.get("dry_day_count", pd.Series(float("nan"), index=result.index))
        heat = result.get("max_temp_c", pd.Series(float("nan"), index=result.index))
        weather_stress = pd.Series(50.0, index=result.index)
        if dry.notna().any():
            weather_stress += _normalize(dry.fillna(0), lower_better=True) * 0.50
        if heat.notna().any():
            heat_stress = heat.apply(
                lambda x: max(0, min(100, (x - EXTREME_HEAT_THRESHOLD_C) / 10.0 * 100))
            )
            weather_stress += (100 - heat_stress.fillna(0)) * 0.50
        weather_stress = weather_stress / 2.0
    stress += weather_stress * CROP_STRESS_WEIGHTS["weather_stress"]

    result["crop_stress_apparent"] = stress.clip(0, 100)
    result["crop_stress_confidence"] = result["valid_ndvi_observations"].apply(
        lambda v: CONFIDENCE_HIGH if v >= 5
        else (CONFIDENCE_MODERATE if v >= 3 else CONFIDENCE_LOW)
    )

    no_ndvi = result["ndvi_condition_score"].isna()
    result.loc[no_ndvi, "crop_stress_confidence"] = CONFIDENCE_INSUFFICIENT

    return result


# ──────────────────────────────────────────────
# Soil Condition Screening Score (0-100)
# ──────────────────────────────────────────────

# Organic matter reference bands (mineral agricultural soils).
# Each entry: (lower_bound, upper_bound, score_at_lower, score_at_upper).
# Linear interpolation is used within each band.
# These are provisional and should be adjusted for local soil orders,
# texture, climate, and production system.
_OM_REFERENCE_BANDS: list[tuple[float, float, float, float]] = [
    (0.0, 1.0, 0.0, 10.0),
    (1.0, 2.0, 10.0, 35.0),
    (2.0, 3.0, 35.0, 65.0),
    (3.0, 4.0, 65.0, 90.0),
    (4.0, 6.0, 90.0, 100.0),
]

# pH suitability reference ranges (general row-crop target).
# Values at or between target_low and target_high score 100.
# Below target_low: linear decline to 0 at extreme_low.
# Above target_high: linear decline to 0 at extreme_high.
_PH_TARGET_LOW = 6.0
_PH_TARGET_HIGH = 7.0
_PH_EXTREME_LOW = 4.5
_PH_EXTREME_HIGH = 8.5

# Available water capacity reference bands (inches total profile).
_AWC_REFERENCE_BANDS: list[tuple[float, float, float, float]] = [
    (0.0, 0.5, 0.0, 10.0),
    (0.5, 1.0, 10.0, 30.0),
    (1.0, 2.0, 30.0, 60.0),
    (2.0, 3.0, 60.0, 85.0),
    (3.0, 5.0, 85.0, 100.0),
]

# CEC reference bands in cmol(+)/kg.
_CEC_REFERENCE_BANDS: list[tuple[float, float, float, float]] = [
    (0.0, 5.0, 0.0, 15.0),
    (5.0, 10.0, 15.0, 35.0),
    (10.0, 15.0, 35.0, 60.0),
    (15.0, 25.0, 60.0, 85.0),
    (25.0, 40.0, 85.0, 100.0),
]


def _score_from_reference_bands(value: float,
                                bands: list[tuple[float, float, float, float]],
                                missing_default: float = 50.0) -> float:
    """Score a continuous variable using reference bands with linear interpolation.

    Each band is (lower_bound, upper_bound, score_at_lower, score_at_upper).
    Values below the first band's lower bound are scored at the first band's
    lower score.  Values above the last band's upper bound are scored at the
    last band's upper score.
    """
    if pd.isna(value):
        return missing_default
    if value < bands[0][0]:
        return bands[0][2]
    if value > bands[-1][1]:
        return bands[-1][3]
    for lo, hi, score_lo, score_hi in bands:
        if lo <= value <= hi:
            frac = (value - lo) / (hi - lo) if hi != lo else 0.0
            return score_lo + frac * (score_hi - score_lo)
    return missing_default


def _om_score(om_pct: float) -> float:
    """Score organic matter on 0-100 using agronomic reference bands."""
    return _score_from_reference_bands(om_pct, _OM_REFERENCE_BANDS)


def _ph_suitability(ph: float,
                    target_low: float = _PH_TARGET_LOW,
                    target_high: float = _PH_TARGET_HIGH,
                    extreme_low: float = _PH_EXTREME_LOW,
                    extreme_high: float = _PH_EXTREME_HIGH) -> float:
    """Score pH suitability on 0-100 with a continuous peak curve.

    Values in [target_low, target_high] score 100.
    Below target_low: linear decline from 100 at target_low to 0 at extreme_low.
    Above target_high: linear decline from 100 at target_high to 0 at extreme_high.
    Values at or beyond extreme bounds score 0.
    """
    if pd.isna(ph):
        return 50.0
    if target_low <= ph <= target_high:
        return 100.0
    if ph < target_low:
        if ph <= extreme_low:
            return 0.0
        return max(0.0, (ph - extreme_low) / (target_low - extreme_low) * 100.0)
    if ph >= extreme_high:
        return 0.0
    return max(0.0, (extreme_high - ph) / (extreme_high - target_high) * 100.0)


def _awc_score(awc_inches: float) -> float:
    """Score available water capacity on 0-100 using reference bands (inches)."""
    return _score_from_reference_bands(awc_inches, _AWC_REFERENCE_BANDS)


def _cec_score(cec_meq100g: float) -> float:
    """Score CEC on 0-100 using reference bands (cmol(+)/kg)."""
    return _score_from_reference_bands(cec_meq100g, _CEC_REFERENCE_BANDS)


def _drainage_score(drainage_class: str) -> float:
    """Score drainage class on a 0-100 scale (higher = better for row crops).

    Mapping is configurable via metric_weights.yaml under the key
    'drainage_mapping' if needed.
    """
    mapping = {
        "Excessively drained": 40,
        "Somewhat excessively drained": 50,
        "Well drained": 90,
        "Moderately well drained": 75,
        "Somewhat poorly drained": 55,
        "Poorly drained": 40,
        "Very poorly drained": 30,
    }
    return mapping.get(str(drainage_class).strip(), 50)


def _soil_confidence_from_components(available_count: int, total_possible: int,
                                     depths_verified: bool = True,
                                     coverage_known: bool = False) -> str:
    """Determine soil score confidence from component availability and traceability.

    Parameters
    ----------
    available_count : int
        Number of soil property components with valid data.
    total_possible : int
        Total components in the scoring model (typically 5).
    depths_verified : bool
        Whether target aggregation depths (0–30 cm surface, 0–100 cm AWC)
        have been verified from processed data.
    coverage_known : bool
        Whether actual SSURGO spatial coverage has been computed.

    Returns
    -------
    str
        One of: High, Moderate, Low, Insufficient data.
    """
    if total_possible < 3:
        return CONFIDENCE_INSUFFICIENT
    base_ratio = available_count / total_possible

    if base_ratio >= 0.8:
        if depths_verified and coverage_known:
            return CONFIDENCE_HIGH
        elif depths_verified:
            return CONFIDENCE_MODERATE
        else:
            return CONFIDENCE_LOW
    elif base_ratio >= 0.6:
        if depths_verified:
            return CONFIDENCE_MODERATE
        else:
            return CONFIDENCE_LOW
    elif base_ratio >= 0.4:
        return CONFIDENCE_LOW
    return CONFIDENCE_INSUFFICIENT


def score_soil_condition(df: pd.DataFrame) -> pd.DataFrame:
    """Soil Condition Screening Score from physical and chemical properties.

    Components and their weights (from SOIL_CONDITION_WEIGHTS):
      - Organic matter (30%): scored via agronomic reference bands (0-30 cm)
      - pH suitability (20%): continuous peak curve (0-30 cm)
      - Available water capacity (20%): scored via reference bands (0-100 cm)
      - Drainage (20%): class-based mapping
      - CEC (10%): scored via reference bands (0-30 cm)

    Returns the input DataFrame augmented with:
      - Component scores (om_score, ph_suitability_score, awc_score,
        drainage_score, cec_score)
      - Final soil_condition_screening_score (0-100)
      - Screening category (Excellent / Good / Fair / Marginal / Poor)
      - Soil metadata (components available/missing, confidence, method)
      - Traceability columns (depth context, AWC validation note, erosion evidence)

    Missing components are dropped and remaining weights are rescaled so the
    score stays on 0-100.
    """
    result = df.copy()
    total_possible = len(SOIL_CONDITION_WEIGHTS)

    for col in ["om_score", "ph_suitability_score", "awc_score",
                "drainage_score", "cec_score"]:
        result[col] = float("nan")

    scores = pd.Series(0.0, index=result.index)
    total_weight = 0.0
    available = []

    # Organic matter (0-30 cm surface)
    om = result.get("organic_matter_pct")
    if om is not None and not om.isna().all():
        w = SOIL_CONDITION_WEIGHTS.get("organic_matter", 0.30)
        om_scores = om.apply(_om_score)
        result["om_score"] = om_scores
        scores += om_scores * w
        total_weight += w
        available.append("organic_matter")

    # pH suitability (0-30 cm surface)
    ph = result.get("soil_ph")
    if ph is not None and not ph.isna().all():
        w = SOIL_CONDITION_WEIGHTS.get("ph_suitability", 0.20)
        ph_scores = ph.apply(_ph_suitability)
        result["ph_suitability_score"] = ph_scores
        scores += ph_scores * w
        total_weight += w
        available.append("ph_suitability")

    # Available water capacity (0-100 cm root zone)
    awc = result.get("available_water_capacity_in")
    if awc is not None and not awc.isna().all():
        w = SOIL_CONDITION_WEIGHTS.get("available_water_capacity", 0.20)
        awc_scores = awc.apply(_awc_score)
        result["awc_score"] = awc_scores
        scores += awc_scores * w
        total_weight += w
        available.append("available_water_capacity")

    # Drainage
    drainage = result.get("drainage_class")
    if drainage is not None:
        w = SOIL_CONDITION_WEIGHTS.get("drainage", 0.20)
        dr_scores = drainage.apply(_drainage_score)
        result["drainage_score"] = dr_scores
        scores += dr_scores * w
        total_weight += w
        available.append("drainage")

    # CEC (0-30 cm surface)
    cec = result.get("cec_meq100g")
    if cec is not None and not cec.isna().all():
        w = SOIL_CONDITION_WEIGHTS.get("cec", 0.10)
        cec_scores = cec.apply(_cec_score)
        result["cec_score"] = cec_scores
        scores += cec_scores * w
        total_weight += w
        available.append("cec")

    if total_weight == 0:
        result["soil_condition_screening_score"] = 50.0
        result["soil_condition_screening_category"] = "Poor"
        result["soil_components_available"] = ""
        result["soil_components_missing"] = ", ".join(SOIL_CONDITION_WEIGHTS.keys())
        result["soil_score_confidence"] = CONFIDENCE_INSUFFICIENT
        result["soil_score_method"] = "No soil data available"
        result["soil_data_coverage_pct"] = 0.0
        result["awc_validation_note"] = "No AWC data — cannot validate"
        result["erosion_evidence_source"] = "No erosion data available"
        return result

    final_scores = scores / total_weight

    result["soil_condition_screening_score"] = final_scores.clip(0, 100)
    result["soil_condition_screening_category"] = assign_soil_screening_category(
        result["soil_condition_screening_score"]
    )
    result["soil_components_available"] = ", ".join(available)
    missing = [k for k in SOIL_CONDITION_WEIGHTS if k not in available]
    result["soil_components_missing"] = ", ".join(missing) if missing else "none"
    n_available = len(available)
    has_depth_columns = all(
        result.get(c) is not None and not result[c].isna().all()
        for c in ["om_depth_cm", "ph_depth_cm", "cec_depth_cm", "awc_profile_depth_cm"]
        if c in result.columns
    )
    has_coverage = result.get("ssurgo_coverage_pct") is not None and not result["ssurgo_coverage_pct"].isna().all()
    result["soil_score_confidence"] = _soil_confidence_from_components(
        n_available, total_possible,
        depths_verified=has_depth_columns,
        coverage_known=has_coverage,
    )
    result["soil_score_method"] = (
        "Soil Condition Screening Score — provisional screening tool based on "
        "available SSURGO-derived properties. Not a substitute for field soil "
        "sampling or a comprehensive soil-health assessment."
    )

    # AWC plateau validation note (per-field)
    def _awc_note(row: pd.Series) -> str:
        awc_val = row.get("available_water_capacity_in")
        awc_sc = row.get("awc_score")
        if pd.isna(awc_val) or pd.isna(awc_sc):
            return "AWC data not available."
        if awc_sc == 100.0:
            return (
                f"AWC raw value {awc_val:.2f} in exceeds the maximum reference threshold "
                f"(5.0 in). Score plateau at 100 is expected for high-AWC soils."
            )
        return f"AWC raw value {awc_val:.2f} in scores {awc_sc:.0f}/100."

    result["awc_validation_note"] = result.apply(_awc_note, axis=1)

    # Erosion evidence
    erosion = result.get("erosion_risk")
    if erosion is not None and not erosion.isna().all():
        kf = result.get("k_factor")
        has_k = kf.notna().any() if kf is not None else False
        source = "SSURGO K-factor (kwfact)"
        if has_k:
            k_min = kf.min()
            k_max = kf.max()
            source += f" — K-factor range [{k_min:.3f}, {k_max:.3f}]"
        result["erosion_evidence_source"] = source
    else:
        result["erosion_evidence_source"] = "Not computed — K-factor not available"

    # Soil depth traceability
    result["om_depth_cm"] = result.get("om_depth_cm", 30)
    result["ph_depth_cm"] = result.get("ph_depth_cm", 30)
    result["cec_depth_cm"] = result.get("cec_depth_cm", 30)
    result["awc_profile_depth_cm"] = result.get("awc_profile_depth_cm", 100)

    result["soil_data_coverage_pct"] = round(n_available / total_possible * 100, 1)

    return result


# ──────────────────────────────────────────────
# Conservation Priority Score (0-100)
# ──────────────────────────────────────────────


def score_conservation_priority(df: pd.DataFrame) -> pd.DataFrame:
    """Conservation Priority Score — higher = higher priority.

    Components (weights from CONSERVATION_PRIORITY_WEIGHTS):
      - Erosion risk (30%)
      - Drainage concern (30%): 100 - drainage_score
      - Low soil condition (25%): 100 - soil_condition_screening_score
      - Sand content (15%): sand_pct scored via reference bands

    Missing components are dropped and remaining weights rescaled.
    Higher scores indicate greater conservation concern.
    """
    result = df.copy()
    total_possible = len(CONSERVATION_PRIORITY_WEIGHTS)

    priority = pd.Series(0.0, index=result.index)
    total_weight = 0.0
    available = []

    erosion = result.get("erosion_risk")
    if erosion is not None and not erosion.isna().all():
        valid_erosion = erosion.dropna()
        if not valid_erosion.empty and (valid_erosion != "unavailable").any():
            er_map = {"none": 0, "low": 25, "moderate": 50, "high": 75, "severe": 100}
            er_scores = erosion.map(er_map).fillna(0)
            priority += er_scores * CONSERVATION_PRIORITY_WEIGHTS["erosion_risk"]
            total_weight += CONSERVATION_PRIORITY_WEIGHTS["erosion_risk"]
            available.append("erosion_risk")

    drainage = result.get("drainage_class")
    if drainage is not None:
        dr_concern = 100 - drainage.apply(_drainage_score)
        priority += dr_concern * CONSERVATION_PRIORITY_WEIGHTS["drainage_concern"]
        total_weight += CONSERVATION_PRIORITY_WEIGHTS["drainage_concern"]
        available.append("drainage_concern")

    soil_condition = result.get("soil_condition_screening_score")
    if soil_condition is not None and not soil_condition.isna().all():
        priority += (100 - soil_condition.fillna(soil_condition.median())) * CONSERVATION_PRIORITY_WEIGHTS["soil_condition_screening"]
        total_weight += CONSERVATION_PRIORITY_WEIGHTS["soil_condition_screening"]
        available.append("soil_condition_screening")

    sand = result.get("sand_pct")
    if sand is not None and not sand.isna().all():
        sd_scores = sand.apply(lambda v: _score_from_reference_bands(
            v, [(0, 15, 0, 10), (15, 30, 10, 30), (30, 50, 30, 55),
                 (50, 70, 55, 80), (70, 100, 80, 100)]
        ))
        priority += sd_scores * CONSERVATION_PRIORITY_WEIGHTS["sand_content"]
        total_weight += CONSERVATION_PRIORITY_WEIGHTS["sand_content"]
        available.append("sand_content")

    if total_weight == 0:
        result["conservation_priority_score"] = 50.0
        result["conservation_components_available"] = ""
        result["conservation_components_missing"] = ", ".join(CONSERVATION_PRIORITY_WEIGHTS.keys())
        result["conservation_confidence"] = CONFIDENCE_INSUFFICIENT
        return result

    # Rescale for missing components — scores are already 0-100,
    # so only divide by sum of available weights.
    final_priority = priority / total_weight

    result["conservation_priority_score"] = final_priority.clip(0, 100)
    result["conservation_components_available"] = ", ".join(available)
    missing = [k for k in CONSERVATION_PRIORITY_WEIGHTS if k not in available]
    result["conservation_components_missing"] = ", ".join(missing) if missing else "none"
    n_avail = len(available)
    result["conservation_confidence"] = _soil_confidence_from_components(
        n_avail, total_possible
    )

    return result


# ──────────────────────────────────────────────
# Risk and priority categories
# ──────────────────────────────────────────────


def assign_risk_categories(fis: pd.Series) -> pd.DataFrame:
    """Assign risk, stress, and priority categories from continuous scores.

    Bands are set to reflect relative analytical confidence given typical
    data quality and field counts.  They are not agronomic prescriptions.

    FIS >= 65 → Low risk / Low stress / Routine
    FIS >= 50 → Moderate risk / Low stress / Routine
    FIS >= 35 → Elevated risk / Moderate stress / Monitor
    FIS < 35  → High risk / High stress / Priority
    """
    categories = pd.DataFrame(index=fis.index)

    conditions = [
        (fis >= 65),
        (fis >= 50),
        (fis >= 35),
    ]
    risk_choices = ["Low", "Moderate", "Elevated"]
    categories["risk_category"] = np.select(conditions, risk_choices, default="High")

    stress_conditions = [
        (fis >= 50),
        (fis >= 35),
    ]
    stress_choices = ["Low", "Moderate"]
    categories["stress_category"] = np.select(stress_conditions, stress_choices, default="High")

    cons_conditions = [
        (fis >= 50),
        (fis >= 35),
    ]
    cons_choices = ["Routine", "Monitor"]
    categories["priority_category"] = np.select(cons_conditions, cons_choices, default="Priority")

    return categories


# ──────────────────────────────────────────────
# Soil Screening Categories
# ──────────────────────────────────────────────


def assign_soil_screening_category(score: pd.Series) -> pd.Series:
    """Assign soil screening category based on the 0-100 composite score.

    Thresholds (from config/soil_screening_categories):
      >= 80  → Strong screening condition
      >= 65  → Moderately strong screening condition
      >= 50  → Moderate screening condition
      >= 35  → Constrained screening condition
      < 35   → Poor screening condition
    """
    sorted_cats = sorted(SOIL_SCREENING_CATEGORIES.items(), key=lambda x: x[1])
    labels = [label for label, _ in sorted_cats]
    bounds = [thresh for _, thresh in sorted_cats]
    bins = [-1] + bounds[1:] + [101]
    return pd.cut(score, bins=bins, labels=labels, right=False).astype(str)


# ──────────────────────────────────────────────
# Main entry point
# ──────────────────────────────────────────────


def compute_all_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all composite metrics and return a scored DataFrame.

    Pipeline:
      1. Extract raw NDVI stats (mean_ndvi, peak_ndvi, ndvi_cv, etc.)
      2. Compute NDVI Condition Score (crop-aware, 0-100)
      3. Compute Stability Score (from CV, independent)
      4. Compute Weather Suitability Score
      5. Compute Soil Health Score
      6. Compute Crop Stress Indicator (multi-component)
      7. Compute Conservation Priority Score
      8. Compute Field Intelligence Score (weighted FIS)
      9. Assign risk/priority categories
    """
    result = df.copy()

    # Step 1: Raw NDVI stats
    result = extract_ndvi_stats(result)

    # Step 2: NDVI Condition Score
    result = score_ndvi_condition(result)

    # Step 3: Stability Score
    result["stability_score"] = score_stability(result)

    # Step 4: Weather Suitability Score
    result["weather_suitability_score"] = score_weather(result)

    # Step 5: Soil Condition Screening Score (returns DataFrame with component columns)
    result = score_soil_condition(result)

    # Step 6: Crop Stress Indicator
    result = score_crop_stress(result)

    # Step 7: Conservation Priority Score (returns DataFrame with metadata)
    result = score_conservation_priority(result)

    # Step 8: Field Intelligence Score — per-field rescaling for missing components
    component_cols = {
        "crop_health": "ndvi_condition_score",
        "stability": "stability_score",
        "soil_condition": "soil_condition_screening_score",
        "weather_suitability": "weather_suitability_score",
    }

    fis = pd.Series(0.0, index=result.index)
    fis_components = pd.Series("", index=result.index).astype(object)

    for idx in result.index:
        score = 0.0
        active = []
        for key, col in component_cols.items():
            val = result.loc[idx, col]
            if pd.notna(val):
                score += val * FIS_WEIGHTS[key]
                active.append(key)
        if active:
            tw = sum(FIS_WEIGHTS[k] for k in active)
            fis.loc[idx] = score / tw
        fis_components.at[idx] = ", ".join(active) if active else "none"

    result["fis_score"] = fis.clip(0, 100)
    result["fis_components_available"] = fis_components

    result["fis_confidence"] = result["valid_ndvi_observations"].apply(
        lambda v: CONFIDENCE_HIGH if v >= 5
        else (CONFIDENCE_MODERATE if v >= 3 else CONFIDENCE_LOW)
    )

    # Row-crop-only FIS (excludes non-row-crop fields)
    result["row_crop_fis_score"] = float("nan")
    result["row_crop_fis_rank"] = float("nan")
    n_row_crop = 0
    row_crop_mean_fis = float("nan")
    if len(result) > 0 and "crop_name" in result.columns:
        row_crop_mask = result["crop_name"].apply(_is_row_crop)
        n_row_crop = int(row_crop_mask.sum())
        if n_row_crop > 0:
            fis_rc = pd.Series(float("nan"), index=result.index)
            for idx in result.index:
                if not row_crop_mask.loc[idx]:
                    continue
                score = 0.0
                active = []
                for key, col in component_cols.items():
                    val = result.loc[idx, col]
                    if pd.notna(val):
                        score += val * FIS_WEIGHTS[key]
                        active.append(key)
                if active:
                    tw = sum(FIS_WEIGHTS[k] for k in active)
                    fis_rc.loc[idx] = score / tw
            result["row_crop_fis_score"] = fis_rc.clip(0, 100)
            # Rank row-crop fields separately
            rc_scores = result.loc[row_crop_mask, "row_crop_fis_score"].dropna()
            if len(rc_scores) > 0:
                ranks = rc_scores.rank(ascending=False, method="min")
                result.loc[row_crop_mask, "row_crop_fis_rank"] = ranks.values
                row_crop_mean_fis = round(float(rc_scores.mean()), 1)

    # Store row-crop summary stats (same value broadcast to all rows for metadata)
    result["n_row_crop_fields"] = n_row_crop
    result["row_crop_mean_fis"] = row_crop_mean_fis
    # Count non-row-crop reference fields
    result["n_reference_fields"] = len(result) - n_row_crop

    # Step 9: Risk/priority categories
    cats = assign_risk_categories(result["fis_score"])
    for col in cats.columns:
        result[col] = cats[col]

    # Round scores for cleaner output
    score_cols = [
        "ndvi_condition_score", "stability_score", "soil_condition_screening_score",
        "weather_suitability_score", "crop_stress_apparent",
        "conservation_priority_score", "fis_score", "row_crop_fis_score",
        "om_score", "ph_suitability_score", "awc_score",
        "drainage_score", "cec_score", "row_crop_fis_score",
    ]
    for col in score_cols:
        if col in result.columns:
            result[col] = result[col].round(1)

    return result
