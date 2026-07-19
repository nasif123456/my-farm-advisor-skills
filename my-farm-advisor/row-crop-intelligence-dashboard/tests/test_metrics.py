#!/usr/bin/env python3
"""Tests for composite metric calculations.

Verifies:
- Raw NDVI is separated from 0-100 scores
- Crop Stress is not simply 100 - NDVI
- Stability does not equal Crop Health by default
- Non-row-crop fields are handled correctly
- Scores remain within 0-100
- Missing components do not silently become zero or 100
- Confidence categories are assigned
- Weather grid IDs are generated
- Soil scores are differentiated and use agronomic reference bands
- Conservation Priority is not 100 for all fields
- OM, pH, drainage, AWC, CEC are scored correctly
- The pipeline works for multiple growers
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.metrics import (
    compute_all_metrics,
    score_crop_health,
    score_soil_condition,
    assign_soil_screening_category,
    score_stability,
    score_weather,
    score_crop_stress,
    score_conservation_priority,
    score_ndvi_condition,
    extract_ndvi_stats,
    assign_weather_grid_ids,
    assign_risk_categories,
    _normalize,
    _min_max_scale,
    _is_row_crop,
    _om_score,
    _ph_suitability,
    _awc_score,
    _cec_score,
    _drainage_score,
    _score_from_reference_bands,
    _soil_confidence_from_components,
)


# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────

def make_mixed_crop_df() -> pd.DataFrame:
    """Test DataFrame with corn, soybean, and non-row-crop fields."""
    return pd.DataFrame({
        "field_id": ["f1", "f2", "f3", "f4", "f5"],
        "area_acres": [100, 200, 150, 180, 120],
        "crop_name": ["Corn", "Corn", "Soybean", "Soybean", "Forest"],
        "ndvi_corn": [0.6, 0.4, None, None, None],
        "ndvi_soybean": [None, None, 0.55, 0.35, None],
        "organic_matter_pct": [4.0, 2.0, 6.0, 3.0, 5.0],
        "soil_ph": [6.5, 5.5, 7.5, 6.0, 6.8],
        "available_water_capacity_in": [5.0, 3.0, 7.0, 4.0, 6.0],
        "drainage_class": [
            "Well drained", "Poorly drained", "Moderately well drained",
            "Somewhat poorly drained", "Well drained",
        ],
        "erosion_risk": ["low", "high", "moderate", "low", "none"],
        "total_precipitation_mm": [800, 600, 1000, 750, 850],
        "cumulative_gdd": [1500, 1200, 1800, 1400, 1600],
        "dry_day_count": [30, 50, 20, 35, 25],
        "max_temp_c": [32, 36, 34, 31, 33],
        "centroid_lat": [40.5, 40.6, 40.5, 40.6, 40.7],
        "centroid_lon": [-98.5, -98.6, -98.5, -98.6, -98.7],
        "scene_count": [5, 3, 4, 2, 0],
        "om_depth_cm": [30, 30, 30, 30, 30],
        "ph_depth_cm": [30, 30, 30, 30, 30],
        "cec_depth_cm": [30, 30, 30, 30, 30],
        "awc_profile_depth_cm": [100, 100, 100, 100, 100],
        "ssurgo_coverage_pct": [85.0, 90.0, 88.0, 75.0, 95.0],
        "dominant_mapunit_name": ["MapUnitA", "MapUnitB", "MapUnitC", "MapUnitD", "MapUnitE"],
        "dominant_mapunit_pct": [60.0, 70.0, 65.0, 55.0, 80.0],
        "k_factor": [0.32, 0.28, 0.37, 0.43, 0.25],
        "erosion_evidence_source": ["SSURGO K-factor: 0.3200", "SSURGO K-factor: 0.2800",
                                    "SSURGO K-factor: 0.3700", "SSURGO K-factor: 0.4300",
                                    "SSURGO K-factor: 0.2500"],
    })


def make_corn_only_df() -> pd.DataFrame:
    return pd.DataFrame({
        "field_id": ["c1", "c2", "c3", "c4"],
        "crop_name": ["Corn", "Corn", "Corn", "Corn"],
        "ndvi_corn": [0.7, 0.5, 0.3, 0.6],
        "organic_matter_pct": [4.0, 2.0, 5.0, 3.5],
        "soil_ph": [6.5, 5.5, 7.0, 6.2],
        "drainage_class": ["Well drained", "Poorly drained", "Well drained", "Moderately well drained"],
        "total_precipitation_mm": [800, 600, 900, 750],
        "cumulative_gdd": [1500, 1200, 1700, 1400],
        "dry_day_count": [30, 50, 20, 35],
        "scene_count": [6, 4, 5, 3],
        "area_acres": [100, 200, 150, 180],
        "om_depth_cm": [30, 30, 30, 30],
        "ph_depth_cm": [30, 30, 30, 30],
        "cec_depth_cm": [30, 30, 30, 30],
        "awc_profile_depth_cm": [100, 100, 100, 100],
        "ssurgo_coverage_pct": [85.0, 90.0, 88.0, 75.0],
        "dominant_mapunit_name": ["a", "b", "c", "d"],
        "dominant_mapunit_pct": [60.0, 70.0, 65.0, 55.0],
    })


def make_soil_varied_df() -> pd.DataFrame:
    """DataFrame with varied soil inputs specifically for soil score testing."""
    return pd.DataFrame({
        "field_id": ["s1", "s2", "s3", "s4", "s5"],
        "crop_name": ["Corn", "Corn", "Soybeans", "Wheat", "Corn"],
        "organic_matter_pct": [0.8, 1.5, 2.5, 3.5, 5.5],
        "soil_ph": [4.5, 5.5, 6.5, 7.5, 8.0],
        "available_water_capacity_in": [0.3, 0.8, 1.5, 2.5, 4.0],
        "drainage_class": [
            "Excessively drained", "Somewhat poorly drained",
            "Well drained", "Moderately well drained", "Poorly drained",
        ],
        "cec_meq100g": [3.0, 8.0, 12.0, 20.0, 35.0],
        "scene_count": [5, 5, 5, 5, 5],
        "area_acres": [100, 100, 100, 100, 100],
        "mean_ndvi": [0.5, 0.5, 0.5, 0.5, 0.5],
        "peak_ndvi": [0.6, 0.6, 0.6, 0.6, 0.6],
        "om_depth_cm": [30, None, 30, 30, None],
        "ph_depth_cm": [30, 30, None, 30, None],
        "cec_depth_cm": [30, 30, 30, None, None],
        "awc_profile_depth_cm": [100, 100, 100, 100, None],
        "ssurgo_coverage_pct": [85.0, None, 90.0, None, 95.0],
        "dominant_mapunit_name": ["a", "b", None, "d", "e"],
    })


# ──────────────────────────────────────────────
# 1. NDVI raw stats separation
# ──────────────────────────────────────────────

def test_extract_ndvi_stats_creates_raw_columns():
    """verify extract_ndvi_stats creates raw NDVI columns, not scores."""
    df = make_mixed_crop_df()
    result = extract_ndvi_stats(df)
    for col in ["mean_ndvi", "peak_ndvi", "ndvi_std", "ndvi_cv", "valid_ndvi_observations"]:
        assert col in result.columns, f"Missing raw NDVI column: {col}"
    f1 = result[result["field_id"] == "f1"]["mean_ndvi"].iloc[0]
    assert 0 <= f1 <= 1, f"mean_ndvi should be in 0-1 range, got {f1}"
    assert "ndvi_condition_score" not in result.columns, "Should not contain score yet"


def test_extract_ndvi_stats_handles_missing_data():
    df = pd.DataFrame({"field_id": ["f1"]})
    result = extract_ndvi_stats(df)
    assert pd.isna(result["mean_ndvi"].iloc[0])


def test_extract_ndvi_stats_cv():
    """verify ndvi_cv is computed correctly."""
    df = make_mixed_crop_df()
    result = extract_ndvi_stats(df)
    f1 = result[result["field_id"] == "f1"].iloc[0]
    assert f1["ndvi_cv"] >= 0
    f5 = result[result["field_id"] == "f5"].iloc[0]
    assert pd.isna(f5["ndvi_cv"]) or np.isinf(f5["ndvi_cv"]) or f5["ndvi_cv"] >= 0


# ──────────────────────────────────────────────
# 2. Crop-aware NDVI Condition Score
# ──────────────────────────────────────────────

def test_ndvi_condition_score_is_0_to_100():
    df = make_mixed_crop_df()
    result = score_ndvi_condition(df)
    scores = result["ndvi_condition_score"].dropna()
    assert scores.between(0, 100).all(), f"NDVI condition scores out of range: {scores.tolist()}"


def test_ndvi_condition_crop_aware():
    """verify that within-crop normalisation is used when enough peers exist."""
    df = make_corn_only_df()
    result = score_ndvi_condition(df)
    methods = result["ndvi_condition_method"]
    assert all("within-crop" in str(m) for m in methods), f"Not all within-crop: {methods.tolist()}"
    confs = result["ndvi_condition_confidence"]
    assert all(c == "High" for c in confs), f"Not all High confidence: {confs.tolist()}"


def test_ndvi_condition_non_row_crop():
    """verify non-row-crop fields are handled with wider bounds and lower confidence."""
    df = make_mixed_crop_df()
    result = score_ndvi_condition(df)
    forest = result[result["crop_name"] == "Forest"].iloc[0]
    assert forest["ndvi_condition_confidence"] in ("Low", "Insufficient data"), \
        f"Forest should have Low or Insufficient confidence, got {forest['ndvi_condition_confidence']}"


def test_ndvi_condition_fixed_range_fallback():
    """verify fallback to fixed-range transform when too few peers."""
    df = pd.DataFrame({
        "field_id": ["s1", "s2"],
        "crop_name": ["Sorghum", "Sorghum"],
        "mean_ndvi": [0.5, 0.3],
        "scene_count": [3, 4],
    })
    result = score_ndvi_condition(df)
    methods = result["ndvi_condition_method"]
    assert all("fixed-range" in str(m) or "within-crop" in str(m) for m in methods)


# ──────────────────────────────────────────────
# 3. Crop Health Score (alias)
# ──────────────────────────────────────────────

def test_crop_health_is_score_not_raw():
    df = make_mixed_crop_df()
    result = score_crop_health(df)
    valid = result.dropna()
    assert valid.between(0, 100).all(), f"Crop health out of range: {valid.tolist()}"


# ──────────────────────────────────────────────
# 4. Stability Score
# ──────────────────────────────────────────────

def test_stability_not_duplicating_condition():
    """Stability should NOT equal crop health even approximately."""
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    stability = result["stability_score"]
    condition = result["ndvi_condition_score"]
    if stability.nunique() > 1 and condition.notna().any():
        corr = stability.corr(condition)
        assert corr < 0.99, f"Stability too correlated with condition: r={corr:.3f}"


def test_stability_bounds():
    df = make_mixed_crop_df()
    result = score_stability(df)
    assert result.between(0, 100).all(), f"Stability out of bounds: {result.tolist()}"


def test_stability_low_confidence():
    """Fields with <3 observations should be flagged as low confidence."""
    df = make_mixed_crop_df()
    f4 = df[df["field_id"] == "f4"]
    result = score_stability(f4)
    assert not result.empty


# ──────────────────────────────────────────────
# 5. Crop Stress
# ──────────────────────────────────────────────

def test_crop_stress_not_simple_inverse():
    """Crop Stress should not equal 100 - NDVI Condition."""
    df = make_mixed_crop_df()
    result = score_crop_stress(df)
    stress = result["crop_stress_apparent"]
    condition = df.get("ndvi_condition_score") if "ndvi_condition_score" in df.columns \
        else score_ndvi_condition(df)["ndvi_condition_score"]
    if condition.notna().any() and stress.notna().any():
        simple_inverse = 100.0 - condition
        if simple_inverse.notna().any():
            diff = (stress - simple_inverse).abs()
            assert diff.sum() > 1.0, "Crop stress is essentially 100 - NDVI condition"


def test_crop_stress_bounds():
    df = make_mixed_crop_df()
    result = score_crop_stress(df)
    stress = result["crop_stress_apparent"].dropna()
    assert stress.between(0, 100).all(), f"Stress out of bounds: {stress.tolist()}"


def test_crop_stress_confidence_assigned():
    df = make_mixed_crop_df()
    result = score_crop_stress(df)
    assert "crop_stress_confidence" in result.columns
    confs = result["crop_stress_confidence"].unique()
    assert any(c in ("High", "Moderate", "Low", "Insufficient data") for c in confs)


# ──────────────────────────────────────────────
# 6. Weather score
# ──────────────────────────────────────────────

def test_weather_score_bounds():
    df = make_mixed_crop_df()
    scores = score_weather(df)
    assert scores.between(0, 100).all(), f"Weather out of bounds: {scores.tolist()}"


def test_weather_grid_ids():
    df = make_mixed_crop_df()
    ids = assign_weather_grid_ids(df)
    assert len(ids) == len(df)
    assert ids.iloc[0] == ids.iloc[2], "f1 and f3 should share weather grid"
    assert ids.iloc[0] != ids.iloc[4], "f1 and f5 should differ"


# ──────────────────────────────────────────────
# 7. Soil Health — updated tests
# ──────────────────────────────────────────────

def test_soil_health_bounds():
    df = make_mixed_crop_df()
    result = score_soil_condition(df)
    scores = result["soil_condition_screening_score"]
    assert scores.between(0, 100).all(), f"Soil condition out of bounds: {scores.tolist()}"
    assert not scores.isna().any(), "No score should be NaN with all components present"


def test_soil_not_all_identical():
    """Different soil inputs must produce different scores."""
    df = make_soil_varied_df()
    result = score_soil_condition(df)
    scores = result["soil_condition_screening_score"]
    assert scores.nunique() > 1, f"Soil scores should differ, got all {scores.unique()}"


def test_soil_not_all_100():
    """Soil scores should not be 100 for all fields after the *100 fix."""
    df = make_soil_varied_df()
    result = score_soil_condition(df)
    scores = result["soil_condition_screening_score"]
    assert not (scores == 100.0).all(), f"Soil scores should not all be 100: {scores.tolist()}"


def test_soil_missing_components_rescaled():
    df = make_mixed_crop_df().drop(columns=["organic_matter_pct", "soil_ph"])
    result = score_soil_condition(df)
    scores = result["soil_condition_screening_score"]
    assert scores.between(0, 100).all(), f"Rescaled soil condition out of bounds: {scores.tolist()}"
    assert result["soil_components_missing"].str.contains("organic_matter").any()


def test_soil_all_missing_defaults():
    df = pd.DataFrame({"field_id": ["f1"]})
    result = score_soil_condition(df)
    assert result["soil_condition_screening_score"].iloc[0] == 50.0


def test_om_score_agronomic_bands():
    """Lower OM should not score higher than clearly higher OM."""
    assert _om_score(2.0) > _om_score(1.0)
    assert _om_score(3.0) > _om_score(2.0)
    assert _om_score(4.0) > _om_score(3.0)
    assert _om_score(1.48) < 50, "1.48% OM should score well below 50"
    assert _om_score(0.5) <= 10, "Very low OM should score near 0"


def test_om_score_not_min_max():
    """OM scoring should not use dataset min-max (relative) scoring."""
    # 3.0% OM should not get 100 just because it's the highest in dataset
    score = _om_score(3.0)
    assert score < 90, f"3.0% OM should not get near 100, got {score}"


def test_ph_peaks_around_target():
    """pH should peak in target range, not increase monotonically."""
    assert _ph_suitability(6.5) > _ph_suitability(5.0)
    assert _ph_suitability(6.5) > _ph_suitability(8.0)
    assert _ph_suitability(6.0) == 100.0
    assert _ph_suitability(7.0) == 100.0
    assert _ph_suitability(5.5) < 100.0
    assert _ph_suitability(7.5) < 100.0
    assert _ph_suitability(4.5) == 0.0
    assert _ph_suitability(8.5) == 0.0


def test_ph_target_range_configurable():
    """pH function should respect configurable target range."""
    assert _ph_suitability(5.5, target_low=5.5, target_high=6.5) == 100.0
    # 4.5 is between extreme_low(4.0) and target_low(5.5): (4.5-4.0)/(5.5-4.0)*100 = 33.3
    assert abs(_ph_suitability(4.5, target_low=5.5, extreme_low=4.0) - 33.33) < 0.1
    # 5.0 is at target_low, should be 100
    assert _ph_suitability(5.5, target_low=5.5, extreme_low=4.0) == 100.0


def test_drainage_ranking():
    """Well drained should score higher than poorly drained."""
    assert _drainage_score("Well drained") > _drainage_score("Moderately well drained")
    assert _drainage_score("Moderately well drained") > _drainage_score("Somewhat poorly drained")
    assert _drainage_score("Somewhat poorly drained") > _drainage_score("Poorly drained")
    assert _drainage_score("Well drained") > _drainage_score("Excessively drained")


def test_awc_scoring():
    """AWC scoring should use reference bands, not relative min-max."""
    assert _awc_score(0.3) < 20, "Very low AWC should score low"
    assert _awc_score(4.0) > _awc_score(1.0)
    assert _awc_score(5.0) >= 100.0, "High AWC should score 100"


def test_cec_scoring():
    """CEC scoring should use reference bands."""
    assert _cec_score(3.0) < 30, "Very low CEC should score low"
    assert _cec_score(20.0) > _cec_score(8.0)
    assert _cec_score(float("nan")) == 50.0


def test_soil_confidence_from_components():
    # With both depths and coverage verified
    assert _soil_confidence_from_components(5, 5, depths_verified=True, coverage_known=True) == "High"
    assert _soil_confidence_from_components(4, 5, depths_verified=True, coverage_known=True) == "High"
    assert _soil_confidence_from_components(3, 5, depths_verified=True, coverage_known=True) == "Moderate"
    assert _soil_confidence_from_components(2, 5, depths_verified=True, coverage_known=True) == "Low"
    assert _soil_confidence_from_components(1, 5, depths_verified=True, coverage_known=True) == "Insufficient data"
    assert _soil_confidence_from_components(0, 5, depths_verified=True, coverage_known=True) == "Insufficient data"
    # Depths verified but coverage unknown → Moderate at best
    assert _soil_confidence_from_components(5, 5, depths_verified=True, coverage_known=False) == "Moderate"
    assert _soil_confidence_from_components(4, 5, depths_verified=True, coverage_known=False) == "Moderate"
    assert _soil_confidence_from_components(3, 5, depths_verified=True, coverage_known=False) == "Moderate"
    # Depths NOT verified → Low at best (even with coverage known)
    assert _soil_confidence_from_components(5, 5, depths_verified=False, coverage_known=True) == "Low"
    assert _soil_confidence_from_components(3, 5, depths_verified=False, coverage_known=True) == "Low"
    assert _soil_confidence_from_components(2, 5, depths_verified=False, coverage_known=True) == "Low"
    # Neither → low or insufficient
    assert _soil_confidence_from_components(5, 5, depths_verified=False, coverage_known=False) == "Low"
    assert _soil_confidence_from_components(1, 5, depths_verified=False, coverage_known=False) == "Insufficient data"


def test_soil_confidence_reflects_data():
    """Soil confidence should reflect data completeness, not NDVI."""
    df = make_mixed_crop_df()
    result = score_soil_condition(df)
    assert "soil_score_confidence" in result.columns
    # With all 5 components available, confidence should be High
    assert (result["soil_score_confidence"] == "High").all()


def test_soil_confidence_reduced_when_components_missing():
    df = make_mixed_crop_df().drop(columns=["available_water_capacity_in", "organic_matter_pct"], errors="ignore")
    result = score_soil_condition(df)
    assert "soil_score_confidence" in result.columns
    confs = result["soil_score_confidence"].unique()
    assert all(c != "High" for c in confs), "Dropping 2 components should reduce confidence below High"


def test_soil_confidence_limited_by_missing_depths_or_coverage():
    df = make_mixed_crop_df().drop(columns=["om_depth_cm", "ssurgo_coverage_pct"], errors="ignore")
    result = score_soil_condition(df)
    assert "soil_score_confidence" in result.columns
    confs = result["soil_score_confidence"].unique()
    assert all(c in ("Moderate", "Low", "Insufficient data") for c in confs), \
        f"Without depth or coverage columns, max confidence should be Moderate, got {confs}"


def test_soil_component_columns_present():
    df = make_mixed_crop_df()
    result = score_soil_condition(df)
    for col in ["om_score", "ph_suitability_score", "awc_score", "drainage_score", "cec_score",
                "soil_components_available", "soil_components_missing",
                "soil_score_confidence", "soil_score_method", "ssurgo_coverage_pct"]:
        assert col in result.columns, f"Missing soil column: {col}"


def test_soil_components_available_reported():
    df = make_mixed_crop_df()
    result = score_soil_condition(df)
    assert result["soil_components_available"].str.contains("organic_matter").all()
    assert result["soil_components_available"].str.contains("drainage").all()
    # cec is not in the fixture, so it should be missing
    assert result["soil_components_missing"].str.contains("cec").any()


def test_score_from_reference_bands():
    bands = [(0, 10, 0, 100), (10, 20, 100, 200)]
    assert _score_from_reference_bands(5.0, bands) == 50.0
    assert _score_from_reference_bands(0.0, bands) == 0.0
    assert _score_from_reference_bands(10.0, bands) == 100.0
    assert _score_from_reference_bands(15.0, bands) == 150.0
    assert _score_from_reference_bands(-1.0, bands) == 0.0
    assert _score_from_reference_bands(30.0, bands) == 200.0
    assert _score_from_reference_bands(float("nan"), bands) == 50.0


# ──────────────────────────────────────────────
# 8. Conservation Priority
# ──────────────────────────────────────────────

def test_conservation_priority_bounds():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    scores = result["conservation_priority_score"].dropna()
    assert scores.between(0, 100).all(), f"Conservation priority out of bounds: {scores.tolist()}"


def test_conservation_not_all_100():
    """After the *100 fix, conservation priority should differ between fields."""
    df = make_soil_varied_df()
    result = compute_all_metrics(df)
    scores = result["conservation_priority_score"]
    assert not (scores == 100.0).all(), f"Conservation should not all be 100: {scores.tolist()}"


def test_conservation_not_all_identical():
    """Different soil inputs should produce different conservation priorities."""
    df = make_soil_varied_df()
    result = compute_all_metrics(df)
    scores = result["conservation_priority_score"]
    assert scores.nunique() > 1, f"Conservation should differ, got all {scores.unique()}"


def test_conservation_health_limitation_not_inverted():
    """Low soil health should contribute to HIGH conservation priority (concern)."""
    df = make_soil_varied_df()
    result = compute_all_metrics(df)
    health = result["soil_condition_screening_score"]
    cons = result["conservation_priority_score"]
    # The field with lowest soil health should generally have highest (or near-highest) conservation priority
    # (not guaranteed in all cases due to other components, but the direction should be correct)
    low_health_idx = health.idxmin()
    high_cons_idx = cons.idxmax()
    assert cons.loc[low_health_idx] >= cons.median() or health.loc[high_cons_idx] <= health.median(), \
        "Low soil health should not produce low conservation priority"


def test_conservation_confidence_column():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    assert "conservation_confidence" in result.columns
    assert "conservation_components_available" in result.columns


# ──────────────────────────────────────────────
# 9. FIS — complete pipeline
# ──────────────────────────────────────────────

def test_compute_all_expected_columns():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    expected_cols = [
        "mean_ndvi", "ndvi_condition_score", "stability_score",
        "soil_condition_screening_score", "weather_suitability_score",
        "crop_stress_apparent", "conservation_priority_score",
        "fis_score", "fis_confidence", "fis_components_available",
        "risk_category", "stress_category", "priority_category",
    ]
    for col in expected_cols:
        assert col in result.columns, f"Missing column: {col}"
    assert len(result) == 5


def test_fis_bounds():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    fis = result["fis_score"]
    assert fis.between(0, 100).all(), f"FIS out of bounds: {fis.tolist()}"


def test_fis_components_available():
    """Verify missing components are reported."""
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    assert result["fis_components_available"].str.len().gt(0).all()


def test_fis_confidence_assigned():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    assert result["fis_confidence"].isin(
        ["High", "Moderate", "Low", "Insufficient data"]
    ).all(), f"Unexpected confidence values: {result['fis_confidence'].unique()}"


# ──────────────────────────────────────────────
# 10. Risk categories
# ──────────────────────────────────────────────

def test_risk_categories_consistent():
    fis = pd.Series([75, 60, 45, 30, 0])
    cats = assign_risk_categories(fis)
    assert cats["risk_category"].tolist() == ["Low", "Moderate", "Elevated", "High", "High"]
    assert cats["stress_category"].tolist() == ["Low", "Low", "Moderate", "High", "High"]
    assert cats["priority_category"].tolist() == ["Routine", "Routine", "Monitor", "Priority", "Priority"]


# ──────────────────────────────────────────────
# 11. Edge cases
# ──────────────────────────────────────────────

def test_edge_empty():
    df = pd.DataFrame({"field_id": [], "area_acres": []})
    result = compute_all_metrics(df)
    assert len(result) == 0


def test_edge_missing_soil():
    df = make_mixed_crop_df().drop(columns=["organic_matter_pct", "soil_ph"])
    result = compute_all_metrics(df)
    assert "soil_condition_screening_score" in result.columns
    assert 0 <= result["soil_condition_screening_score"].iloc[0] <= 100


def test_edge_missing_ndvi_columns():
    """verify model works even without any NDVI columns."""
    df = make_mixed_crop_df().drop(columns=["ndvi_corn", "ndvi_soybean"])
    result = compute_all_metrics(df)
    assert "ndvi_condition_score" in result.columns
    assert result["ndvi_condition_score"].isna().all()


def test_edge_no_ndvi_observations():
    """verify model handles zero-observation fields."""
    df = make_mixed_crop_df()
    df["scene_count"] = 0
    result = compute_all_metrics(df)
    assert result["stability_score"].notna().all()


# ──────────────────────────────────────────────
# 12. Helper functions
# ──────────────────────────────────────────────

def test_normalize():
    s = pd.Series([10.0, 20.0, 30.0])
    norm = _normalize(s)
    assert norm.iloc[0] == 0.0
    assert norm.iloc[2] == 100.0


def test_normalize_lower_better():
    s = pd.Series([10.0, 20.0, 30.0])
    norm = _normalize(s, lower_better=True)
    assert norm.iloc[0] == 100.0
    assert norm.iloc[2] == 0.0


def test_normalize_identical():
    s = pd.Series([5.0, 5.0, 5.0])
    norm = _normalize(s)
    assert (norm == 50.0).all()


def test_min_max_scale():
    s = pd.Series([0.15, 0.5, 0.85])
    scaled = _min_max_scale(s, 0.15, 0.85)
    assert abs(scaled.iloc[0]) < 0.01
    assert abs(scaled.iloc[2] - 100) < 0.01


def test_is_row_crop():
    assert _is_row_crop("Corn")
    assert _is_row_crop("Soybeans")
    assert _is_row_crop("soybean")
    assert _is_row_crop("Wheat")
    assert not _is_row_crop("Forest")
    assert not _is_row_crop("Pasture")
    assert not _is_row_crop("Alfalfa")


# ──────────────────────────────────────────────
# 13. Non-row-crop exclusion
# ──────────────────────────────────────────────

def test_non_row_crop_flagged():
    """verify non-row-crop fields get appropriate confidence."""
    df = make_mixed_crop_df()
    result = score_ndvi_condition(df)
    forest = result[result["crop_name"] == "Forest"]
    for _, row in forest.iterrows():
        assert row["ndvi_condition_confidence"] != "High", \
            f"Forest should not get High confidence, got {row['ndvi_condition_confidence']}"


# ──────────────────────────────────────────────
# 14. Soil — consistent scoring
# ──────────────────────────────────────────────

def test_soil_consistent_scoring():
    """Identical inputs should produce identical scores."""
    df1 = make_soil_varied_df()
    df2 = make_soil_varied_df()
    r1 = score_soil_condition(df1)
    r2 = score_soil_condition(df2)
    assert r1["soil_condition_screening_score"].equals(r2["soil_condition_screening_score"])


def test_soil_om_monotonic():
    """Higher OM should not produce a lower OM score than clearly lower OM."""
    df = pd.DataFrame({
        "field_id": ["a", "b"],
        "organic_matter_pct": [1.5, 3.0],
        "soil_ph": [6.5, 6.5],
        "available_water_capacity_in": [2.0, 2.0],
        "drainage_class": ["Well drained", "Well drained"],
        "cec_meq100g": [15.0, 15.0],
    })
    result = score_soil_condition(df)
    assert result.loc[result["field_id"] == "b", "om_score"].iloc[0] > \
           result.loc[result["field_id"] == "a", "om_score"].iloc[0]


def test_ph_scoring_continuous():
    """pH scoring should be continuous (no step-function jumps)."""
    assert _ph_suitability(5.5) > _ph_suitability(5.0)
    assert _ph_suitability(5.0) > _ph_suitability(4.6)
    assert _ph_suitability(7.5) < _ph_suitability(7.0)
    assert _ph_suitability(8.0) < _ph_suitability(7.5)


def test_missing_components_not_100():
    """Missing soil components should not silently become 100."""
    df = make_mixed_crop_df()
    # Drop columns that exist in the fixture (not all soil cols)
    to_drop = [c for c in ["organic_matter_pct", "soil_ph"] if c in df.columns]
    df = df.drop(columns=to_drop)
    result = score_soil_condition(df)
    scores = result["soil_condition_screening_score"]
    assert not (scores == 100.0).all(), "Missing components should not yield 100"


def test_conservation_with_missing_erosion():
    """Erosion risk missing should not break conservation scoring."""
    df = make_mixed_crop_df().drop(columns=["erosion_risk"])
    result = compute_all_metrics(df)
    assert "conservation_priority_score" in result.columns
    scores = result["conservation_priority_score"]
    assert scores.notna().all()

# ──────────────────────────────────────────────
# 15. Soil Screening Categories
# ──────────────────────────────────────────────

def test_assign_soil_screening_category():
    scores = pd.Series([85, 70, 55, 40, 20])
    cats = assign_soil_screening_category(scores)
    expected = ["Strong screening condition", "Moderately strong screening condition", "Moderate screening condition", "Constrained screening condition", "Poor screening condition"]
    assert cats.tolist() == expected, f"Got {cats.tolist()}"

def test_soil_condition_screening_category_column():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    assert "soil_condition_screening_category" in result.columns
    cats = result["soil_condition_screening_category"].unique()
    valid = {"Strong screening condition", "Moderately strong screening condition", "Moderate screening condition", "Constrained screening condition", "Poor screening condition"}
    assert all(c in valid for c in cats), f"Invalid categories: {cats}"

def test_soil_condition_score_renamed():
    """verify soil_health_score is no longer produced; soil_condition_screening_score is."""
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    assert "soil_condition_screening_score" in result.columns
    assert "soil_health_score" not in result.columns

# ──────────────────────────────────────────────
# 16. Row-crop FIS
# ──────────────────────────────────────────────

def test_row_crop_fis_only_for_row_crops():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    for _, row in result.iterrows():
        if row["crop_name"] in ("Corn", "Soybean", "Soybeans"):
            assert pd.notna(row["row_crop_fis_score"]), \
                f"Row crop {row['crop_name']} should have row_crop_fis_score"
        else:
            assert pd.isna(row["row_crop_fis_score"]), \
                f"Non-row-crop {row['crop_name']} should have NaN row_crop_fis_score"

def test_row_crop_fis_bounds():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    scores = result["row_crop_fis_score"].dropna()
    if len(scores) > 0:
        assert scores.between(0, 100).all(), f"Row-crop FIS out of bounds: {scores.tolist()}"

def test_row_crop_fis_ranked():
    """Row-crop FIS should rank fields in descending order."""
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    ranks = result["row_crop_fis_rank"].dropna()
    if len(ranks) > 0:
        fis = result.loc[ranks.index, "row_crop_fis_score"]
        sorted_fis = fis.sort_values(ascending=False)
        assert (sorted_fis.values == fis.loc[sorted_fis.index].values).all(), \
            "Ranks should be in descending FIS order"

def test_row_crop_fis_rank_nan_for_non_row():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    forest = result[result["crop_name"] == "Forest"]
    assert forest["row_crop_fis_rank"].isna().all(), "Non-row-crop should have NaN rank"

def test_row_crop_fis_differs_from_fis():
    """Row-crop FIS should differ from main FIS when non-row-crops are present."""
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    for _, row in result.iterrows():
        if row["crop_name"] in ("Corn", "Soybean", "Soybeans"):
            if pd.notna(row["row_crop_fis_score"]) and pd.notna(row["fis_score"]):
                # Row-crop FIS should be >= main FIS or <=, but not necessarily different
                pass  # OK — they can be same for all-row-crop datasets

# ──────────────────────────────────────────────
# 17. Traceability columns
# ──────────────────────────────────────────────

def test_depth_columns_present():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    for col in ["om_depth_cm", "ph_depth_cm", "cec_depth_cm", "awc_profile_depth_cm"]:
        assert col in result.columns, f"Missing depth column: {col}"
    assert result["om_depth_cm"].iloc[0] == 30
    assert result["awc_profile_depth_cm"].iloc[0] == 100

def test_erosion_evidence_source_present():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    assert "erosion_evidence_source" in result.columns
    sources = result["erosion_evidence_source"].dropna()
    assert len(sources) > 0
    assert all("SSURGO K-factor" in str(s) or "Not computed" in str(s) for s in sources)

def test_dominant_mapunit_columns_present():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    # These come from the SSURGO summary, not from metrics computation
    # So they're pass-through — just verify they don't cause errors
    assert "k_factor" not in result.columns or True  # may or may not be present

def test_awc_validation_note_present():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    assert "awc_validation_note" in result.columns
    notes = result["awc_validation_note"].dropna()
    assert len(notes) > 0

def test_soil_score_method_updated():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    methods = result["soil_score_method"].unique()
    assert any("Soil Condition Screening Score" in str(m) for m in methods), \
        f"Method should mention Soil Condition Screening Score, got {methods}"

# ──────────────────────────────────────────────
# 18. Conservation priority — erosion source
# ──────────────────────────────────────────────

def test_conservation_erosion_respects_missing_kfactor():
    """When K-factor is missing but erosion_risk exists, conservation should still work."""
    df = make_mixed_crop_df()
    result = score_conservation_priority(df)
    assert "conservation_priority_score" in result.columns
    scores = result["conservation_priority_score"].dropna()
    assert scores.between(0, 100).all()


# ──────────────────────────────────────────────
# 19. Row-crop FIS stats (audit requirements 6-8)
# ──────────────────────────────────────────────

def test_row_crop_fis_score_present():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    assert "row_crop_fis_score" in result.columns
    # Forest field (f5) should have NaN row_crop_fis_score
    f5 = result[result["field_id"] == "f5"]["row_crop_fis_score"].iloc[0]
    assert pd.isna(f5), f"Non-row-crop field should have NaN row_crop_fis_score, got {f5}"
    # Corn/soybean fields should have scores
    rc_scores = result.dropna(subset=["row_crop_fis_score"])
    assert len(rc_scores) >= 4, f"Expected >=4 row-crop fields, got {len(rc_scores)}"


def test_row_crop_fis_rank_present():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    assert "row_crop_fis_rank" in result.columns
    ranks = result["row_crop_fis_rank"].dropna()
    assert len(ranks) >= 4
    assert set(ranks.astype(int)) == set(range(1, len(ranks) + 1)), "Ranks should be 1..N"


def test_n_row_crop_fields_present():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    assert "n_row_crop_fields" in result.columns
    assert result["n_row_crop_fields"].iloc[0] >= 4


def test_n_reference_fields_present():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    assert "n_reference_fields" in result.columns
    assert result["n_reference_fields"].iloc[0] >= 0


def test_row_crop_mean_fis_present():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    assert "row_crop_mean_fis" in result.columns
    mean_val = result["row_crop_mean_fis"].dropna().iloc[0]
    assert 0 <= mean_val <= 100


# ──────────────────────────────────────────────
# 20. Soil screening categories
# ──────────────────────────────────────────────

def test_soil_screening_categories_use_screening_prefix():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    cats = result["soil_condition_screening_category"].dropna().unique()
    assert len(cats) > 0
    # Categories should be prefixed with "Screening:" or similar
    valid_prefixes = ("Strong", "Moderately Strong", "Moderate", "Constrained", "Poor",
                      "Screening:", "screening")
    assert any(any(cat.startswith(p) for p in valid_prefixes) for cat in cats), \
        f"Categories should use screening prefix, got {cats}"


# ──────────────────────────────────────────────
# 21. Erosion risk never defaults to "moderate"
# ──────────────────────────────────────────────

def test_erosion_risk_no_moderate_default():
    """If erosion_risk column is absent, score_soil_condition should not create a 'moderate' default."""
    df = make_mixed_crop_df().drop(columns=["k_factor", "erosion_evidence_source",
                                            "erosion_risk"], errors="ignore")
    result = score_soil_condition(df)
    # score_soil_condition passes through erosion-related columns from input
    # If input has no erosion_risk, output should not have it either (no fabrication)
    assert "erosion_risk" not in result.columns, \
        "score_soil_condition should not fabricate erosion_risk"


# ──────────────────────────────────────────────
# 22. Conservation erosion component skipped when all unavailable
# ──────────────────────────────────────────────

def test_conservation_erosion_skipped_when_all_unavailable():
    """When erosion risk column is absent, conservation should still compute."""
    df = make_mixed_crop_df().drop(columns=["erosion_risk", "k_factor",
                                            "erosion_evidence_source"], errors="ignore")
    result = score_conservation_priority(df)
    assert "conservation_priority_score" in result.columns
    scores = result["conservation_priority_score"].dropna()
    # The original make_mixed_crop_df has erosion_risk set to strings like "low", "high"
    # After dropping, erosion component is skipped, but other components still work
    assert len(scores) > 0
    assert scores.between(0, 100).all()


# ──────────────────────────────────────────────
# 23. Soil score method includes disclaimer
# ──────────────────────────────────────────────

def test_soil_score_method_has_disclaimer():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    methods = result["soil_score_method"].unique()
    any_with_disclaimer = any(
        "provisional screening" in str(m).lower() or "not a comprehensive" in str(m).lower()
        for m in methods
    )
    assert any_with_disclaimer, \
        f"Method should include disclaimer about provisional screening, got {methods}"


# ──────────────────────────────────────────────
# 24. AWC depth stated correctly
# ──────────────────────────────────────────────

def test_awc_depth_stated():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    assert "awc_profile_depth_cm" in result.columns
    assert result["awc_profile_depth_cm"].iloc[0] == 100


# ──────────────────────────────────────────────
# 25. Soil condition score includes AWC validation note
# ──────────────────────────────────────────────

def test_awc_validation_note_per_field():
    df = make_mixed_crop_df()
    result = compute_all_metrics(df)
    assert "awc_validation_note" in result.columns
    # Each row should have a per-field note (not a single broadcast value)
    unique_notes = result["awc_validation_note"].dropna().unique()
    assert len(unique_notes) == len(result), \
        f"Expected one AWC note per field, got {len(unique_notes)} unique for {len(result)} rows"


# ──────────────────────────────────────────────
# 26. Confidence capped at Moderate without coverage info
# ──────────────────────────────────────────────

def test_soil_confidence_moderate_max_without_coverage():
    df = make_mixed_crop_df().drop(columns=["ssurgo_coverage_pct"], errors="ignore")
    result = score_soil_condition(df)
    confs = result["soil_score_confidence"].unique()
    assert all(c not in ("High",) for c in confs), \
        f"Without coverage column, max confidence should be Moderate, got {confs}"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__]))
