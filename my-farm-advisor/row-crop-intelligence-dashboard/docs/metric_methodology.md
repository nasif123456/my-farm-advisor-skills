# Metric Methodology

## Field Intelligence Score (FIS)

The primary integrated metric for comparing field-level performance.

**Formula:**
```
FIS = 0.45 × NDVI Condition Score (Crop Health)
    + 0.25 × Soil Condition Screening Score
    + 0.20 × Weather Suitability Score
    + 0.10 × Stability Score
```

When one or more components are unavailable, the remaining weights are rescaled so the FIS remains on a 0-100 scale.

**Interpretation:**
- 65–100: Low risk
- 50–64: Moderate risk
- 35–49: Elevated risk
- Below 35: High risk

**Confidence categories:**
- High: ≥5 valid NDVI observations
- Moderate: 3–4 valid NDVI observations
- Low: 1–2 valid NDVI observations
- Insufficient data: no NDVI data

**Limitations:**
- Scores are relative to the fields and data included in the dashboard.
- They do not establish cause and effect.
- Weightings are configurable via `config/metric_weights.yaml`.
- The `fis_components_available` field tracks which components contributed.

---

## NDVI Condition Score (Crop Health)

Crop-aware greenness score (0–100). Raw NDVI values (`mean_ndvi`, range -1 to 1) are kept in separate columns and never replaced by the score.

**Scoring method:**
- **Within-crop normalisation**: when ≥3 fields share the same crop type, NDVI is normalised within that crop group. This is the preferred method.
- **Fixed-range transform** (fallback): when too few same-crop peers exist, a crop-specific reference range is used via `_min_max_scale()`.

**Crop-specific NDVI reference ranges:**
| Crop | Low | High | Notes |
|------|-----|------|-------|
| Corn | 0.15 | 0.85 | Standard row-crop range |
| Soybeans | 0.15 | 0.85 | Same as corn |
| Wheat | 0.15 | 0.80 | Slightly lower peak |
| Cotton | 0.15 | 0.75 | Lower maximum greenness |
| Rice | 0.15 | 0.75 | Similar to cotton |
| Sorghum | 0.15 | 0.80 | Between corn and wheat |
| Non-row-crop / unknown | 0.00 | 1.00 | Wider range = lower confidence |

**Crop awareness:**
- Only row crops (corn, soybeans, wheat, cotton, rice, sorghum) are compared using standardised NDVI expectations.
- Non-row-crop fields (forest, pasture, alfalfa, etc.) are scored with wider reference bounds (0.0–1.0) and flagged as lower analytical confidence.

**Raw NDVI variables (always preserved):**
- `mean_ndvi`: actual mean NDVI value (0–1, displayed as e.g. `0.26`)
- `peak_ndvi`: maximum valid seasonal NDVI
- `ndvi_std`: standard deviation across available crop-specific NDVI columns
- `ndvi_cv`: coefficient of variation (std / mean)
- `valid_ndvi_observations`: number of valid satellite scenes

**Output variables:**
- `ndvi_condition_score`: transformed score (0–100)
- `ndvi_condition_method`: description of scoring approach used
- `ndvi_condition_confidence`: High / Moderate / Low / Insufficient data

---

## Stability Score

Measures consistency of vegetation behaviour across the season. **Independently calculated from the NDVI Condition Score** — it does not use mean NDVI.

**Formula:**
```
Stability = 100 − normalised(NDVI coefficient of variation)
```

- Higher values = more stable seasonal vegetation.
- Uses `ndvi_cv` (coefficient of variation from available NDVI values).
- Fields with fewer than 3 valid NDVI observations default to 50 and are flagged as low confidence.
- When all fields have the same CV value, a standard score of 50 is assigned.

**Notable:** This score was previously a duplicate of the NDVI score when peak data was unavailable. It is now genuinely independent.

---

## Apparent Crop Stress Indicator

Identifies fields with apparent vegetation stress based on multiple satellite-observed patterns.

**Formula:**
```
Apparent Stress = 0.45 × Low NDVI Component
                 + 0.25 × NDVI Decline Component
                 + 0.20 × Variability Component
                 + 0.10 × Weather Stress Component
```

| Component | Weight | Derivation |
|-----------|--------|------------|
| Low NDVI | 0.45 | 100 − ndvi_condition_score |
| NDVI Decline | 0.25 | Normalised peak/mean NDVI ratio (higher ratio = more decline from peak) |
| Variability | 0.20 | Normalised NDVI CV (higher CV = more variable) |
| Weather Stress | 0.10 | 100 − weather_suitability_score, or fallback from dry-day / extreme-heat |

**Terminology:** The indicator uses the term **"apparent stress"** because remote-sensing data cannot diagnose the exact cause (pest, disease, nutrient deficiency, moisture stress, etc.). Results support prioritisation and field scouting, not diagnosis.

**Interpretation:**
- 0–30: Low apparent stress
- 31–60: Moderate apparent stress
- 61–100: High apparent stress (field scouting recommended)

**When time-series information is insufficient (<3 scenes), the decline and variability components default to 50 and the overall stress score is flagged as lower confidence.**

---

## Soil Condition Screening Score

Indicates overall soil condition based on available SSURGO/NRCS variables.
Each component is scored independently on 0–100 using absolute reference bands (not dataset-relative min-max), then combined via a weighted sum.

**Name note:** "Soil Condition Screening Score" is used rather than "Soil Health Score" because the available SSURGO variables (OM, pH, AWC, drainage class, CEC) support a condition screening but not a full soil health diagnosis, which would require biological indicators, aggregate stability, and deeper-profile data.

### Components and Scoring Formulas

| Component | Weight | Scoring Method |
|-----------|--------|----------------|
| Organic Matter | 0.30 | Agronomic reference bands with linear interpolation |
| pH Suitability | 0.20 | Continuous peak curve (target 6.0–7.0, decline to 0 at 4.5 and 8.5) |
| Available Water Capacity (AWC) | 0.20 | Reference bands (inches total profile) |
| Drainage Class | 0.20 | Class mapping (see below) |
| CEC | 0.10 | Reference bands (cmol(+)/kg) |

### Organic Matter Scoring

Scored using agronomic reference bands suitable for mineral agricultural soils in temperate regions. Continuous linear interpolation is applied within each band to avoid abrupt jumps.

| OM Range (%) | Score Range | Interpretation |
|--------------|-------------|----------------|
| 0.0–1.0 | 0–10 | Very low |
| 1.0–2.0 | 10–35 | Low |
| 2.0–3.0 | 35–65 | Moderate |
| 3.0–4.0 | 65–90 | Moderately strong |
| 4.0–6.0 | 90–100 | Strong |
| >6.0 | 100 | Very strong |

**Limitations:** These bands are provisional. Soil texture, climate, drainage, soil order, and production system affect OM interpretation. Scores should be reviewed against local benchmarks.

### pH Suitability Scoring

Scored using a continuous peak curve rather than a step function. The default target range for general row-crop screening is 6.0–7.0:

- Values in [6.0, 7.0]: 100 (optimal)
- Below 6.0: linear decline from 100 at 6.0 to 0 at 4.5
- Above 7.0: linear decline from 100 at 7.0 to 0 at 8.5
- At or beyond extreme bounds (≤4.5 or ≥8.5): 0

The target range and extreme bounds are configurable. Where crop-specific pH targets are available, they should be used in preference to the general row-crop default.

### Available Water Capacity Scoring

Scored using reference bands for total profile available water capacity (inches), as provided by SSURGO:

| AWC Range (in) | Score Range | Interpretation |
|----------------|-------------|----------------|
| 0.0–0.5 | 0–10 | Very limited |
| 0.5–1.0 | 10–30 | Limited |
| 1.0–2.0 | 30–60 | Moderate |
| 2.0–3.0 | 60–85 | Good |
| 3.0–5.0 | 85–100 | Excellent |
| >5.0 | 100 | Very high |

### Drainage Class Scoring

| Drainage Class | Score | Notes |
|----------------|-------|-------|
| Excessively drained | 40 | Drought limitation |
| Somewhat excessively drained | 50 | Mild drought concern |
| Well drained | 90 | Generally optimal |
| Moderately well drained | 75 | Slight limitation |
| Somewhat poorly drained | 55 | Moderate limitation |
| Poorly drained | 40 | Significant limitation |
| Very poorly drained | 30 | Severe limitation |
| Unknown/other | 50 | Default |

Both poor drainage (waterlogging risk) and excessive drainage (drought risk) are scored lower, reflecting genuine limitations.

### CEC Scoring

Scored using reference bands for cation exchange capacity (cmol(+)/kg):

| CEC Range (cmol(+)/kg) | Score Range | Interpretation |
|------------------------|-------------|----------------|
| 0–5 | 0–15 | Very low |
| 5–10 | 15–35 | Low |
| 10–15 | 35–60 | Moderate |
| 15–25 | 60–85 | High |
| 25–40 | 85–100 | Very high |
| >40 | 100 | Excellent |

### Score Calculation

```
Soil Condition Score = (OM_score × 0.30 + pH_score × 0.20 + AWC_score × 0.20 + Drainage_score × 0.20 + CEC_score × 0.10) / sum(available_weights)
```

Each component score is independent (0–100). Missing components are dropped and remaining weights are rescaled so the final score remains on 0–100.

### Output Columns

| Column | Description |
|--------|-------------|
| `soil_condition_screening_score` | Final 0–100 Soil Condition Screening Score |
| `om_score` | Organic matter component score |
| `ph_suitability_score` | pH suitability component score |
| `awc_score` | Available water capacity component score |
| `drainage_score` | Drainage class component score |
| `cec_score` | CEC component score |
| `soil_components_available` | Which components contributed to the score |
| `soil_components_missing` | Which components were missing |
| `soil_score_confidence` | Confidence based on data completeness (see below) |
| `soil_score_method` | Always "Soil Condition Screening Score" |
| `soil_data_coverage_pct` | Percentage of possible components available |

### Soil Confidence

Confidence is based on soil data completeness, not on NDVI observations:

| Available Components | Confidence |
|---------------------|------------|
| 4–5 | High |
| 3 | Moderate |
| 2 | Low |
| 0–1 | Insufficient data |

### Interpretation

- 75–100: Generally favourable profile
- 50–74: Moderate limitations
- Below 50: Higher soil-management priority

### Missing Data

When a component is unavailable, it is dropped and the score is rescaled to the available weight total. If fewer than 3 components are available, the confidence is Low or Insufficient. If no components are available, the score defaults to 50 with Insufficient confidence.

### Soil Variables Preserved

The following raw SSURGO soil variables are retained in the field-level output alongside the derived scores:

- `organic_matter_pct`
- `soil_ph`
- `available_water_capacity_in`
- `cec_meq100g`
- `clay_pct`
- `sand_pct`
- `drainage_class`
- `dominant_soil`
- `erosion_risk`

---

## Weather Suitability Score

Compares weather conditions across fields for the selected growing season. All components are normalised within the current field group (relative ranking).

**Components:**
| Component | Weight | Details |
|-----------|--------|---------|
| GDD Adequacy | 0.40 | Cumulative GDD, capped at 4000, normalised |
| Precipitation Adequacy | 0.30 | Total precipitation, capped at 1200 mm, normalised |
| Dry-Spell Stress | 0.15 | Consecutive dry days, inverted (fewer = better) |
| Extreme-Heat Exposure | 0.15 | Days above 35°C, inverted |

**GDD Calculation:**
```
GDD = max(0, (Tmax + Tmin) / 2 − Tbase)
```

Default base temperature: 10°C (suitable for corn and soybeans). Upper temperature cap: 30°C.

**Weather grid identification:**
Fields sharing the same NASA POWER grid cell are identified via `assign_weather_grid_ids()`, which rounds centroid coordinates to 2 decimal places (~11 km at mid-latitudes). This prevents over-interpretation of field-scale weather differences where none exist.

---

## Conservation Priority Score

Identifies fields that may benefit from conservation or management attention. Higher scores indicate greater concern.

**Components:**
| Component | Weight | Derivation |
|-----------|--------|------------|
| Erosion Risk | 0.30 | Mapped from erosion class (none→0, low→25, moderate→50, high→75, severe→100) |
| Drainage Concern | 0.30 | 100 − drainage_score (higher concern for poorly drained fields) |
| Low Soil Condition | 0.25 | 100 − soil_condition_screening_score (higher concern when soil condition is poor) |
| Sand Content | 0.15 | Scored via reference bands (higher sand = greater drought/leaching concern) |

**Sand content scoring bands:**
| Sand (%) | Score | Interpretation |
|----------|-------|----------------|
| 0–15 | 0–10 | Low concern |
| 15–30 | 10–30 | Slight concern |
| 30–50 | 30–55 | Moderate concern |
| 50–70 | 55–80 | High concern |
| 70–100 | 80–100 | Severe concern |

Missing components are dropped and remaining weights rescaled. The score includes a `conservation_confidence` field and lists available/missing components.

**Evidence-based recommendations:**
Generated interpretations reference the specific evidence that triggered a recommendation:
- "Consider reviewing: drainage class 'Poorly drained'; organic matter (1.8%) below 3%"
- Drainage review is recommended only for poorly drained, very poorly drained, or somewhat poorly drained fields.
- Erosion control is recommended only when erosion risk is high or severe.

**Interpretation:**
- 0–40: Routine monitoring
- 41–60: Monitor — may benefit from assessment
- 61–100: Priority — consider management intervention

---

## Analytical Confidence

Each field receives a confidence rating based on data sufficiency:

| Level | NDVI Observations | Interpretation |
|-------|-------------------|----------------|
| High | ≥5 | Sufficient time-series for reliable stress/variability analysis |
| Moderate | 3–4 | Adequate for basic scoring, variability estimates less reliable |
| Low | 1–2 | Strong limitations on stress and stability scoring |
| Insufficient data | 0 | No NDVI data available |

Confidence fields:
- `fis_confidence`: overall confidence in the Field Intelligence Score (based on NDVI scene count)
- `ndvi_condition_confidence`: confidence in the NDVI Condition Score
- `crop_stress_confidence`: confidence in the Apparent Crop Stress Indicator
- `soil_score_confidence`: confidence in the Soil Condition Screening Score (based on soil data completeness, not NDVI)
- `conservation_confidence`: confidence in the Conservation Priority Score (based on component availability)

---

## Risk and Priority Categories

Derived from the FIS score using consistent thresholds:

| FIS Range | Risk Category | Stress Category | Priority Category |
|-----------|---------------|-----------------|-------------------|
| ≥65 | Low | Low | Routine |
| 50–64 | Moderate | Low | Routine |
| 35–49 | Elevated | Moderate | Monitor |
| <35 | High | High | Priority |

These reflect relative analytical confidence given typical data quality. They are not agronomic prescriptions.

---

## Data Sources

| Data | Source | Resolution | Variables Used |
|------|--------|------------|----------------|
| NDVI | Sentinel-2 (via Planetary Computer) | 10m | Mean NDVI, Peak 95th %ile NDVI, scene count |
| Weather | NASA POWER | ~0.5° grid | Precipitation, Tmin, Tmax, GDD |
| Soil | SSURGO / NRCS | 1:12,000–1:63,360 | OM, pH, AWC, drainage, CEC, sand, erosion |
| Boundaries | OpenStreetMap / field-boundaries skill | Field-level | Geometry, area |
| CDL | USDA NASS | 30m | Crop type per year |

---

## Column Name Changes (from previous version)

| Old Name | New Name | Reason |
|----------|----------|--------|
| `ndvi_score` | `ndvi_condition_score` | Clarify this is a 0-100 score, not raw NDVI |
| `ndvi_corn` / `ndvi_soybean` | `mean_ndvi` | Crop-neutral; raw NDVI values preserved |
| `ndvi_corn_peak_95` / `ndvi_soybean_peak_95` | `peak_ndvi` | Crop-neutral peak NDVI |
| `field_intelligence_score` | `fis_score` | Shorter, consistent |
| `crop_stress_indicator` | `crop_stress_apparent` | Uses "apparent stress" terminology |
| *(new)* | `ndvi_cv` | Coefficient of variation |
| *(new)* | `ndvi_std` | Standard deviation |
| *(new)* | `valid_ndvi_observations` | Scene count |
| *(new)* | `fis_confidence` | Analytical confidence |
| *(new)* | `fis_components_available` | Which FIS components were used |
| *(new)* | `ndvi_condition_method` | Scoring approach per field |
| *(new)* | `weather_grid_id` | NASA POWER grid cell identifier |
| *(new)* | `om_score` | Organic matter component score (0–100) |
| *(new)* | `ph_suitability_score` | pH suitability component score (0–100) |
| *(new)* | `awc_score` | Available water capacity component score (0–100) |
| *(new)* | `drainage_score` | Drainage class component score (0–100) |
| *(new)* | `cec_score` | CEC component score (0–100) |
| *(new)* | `soil_components_available` | Which soil components contributed |
| *(new)* | `soil_components_missing` | Which soil components were missing |
| *(new)* | `soil_score_confidence` | Confidence from soil data completeness |
| *(new)* | `soil_score_method` | Always "Soil Condition Screening Score" |
| *(new)* | `soil_data_coverage_pct` | Percentage of components available |
| *(new)* | `conservation_components_available` | Which conservation components contributed |
| *(new)* | `conservation_confidence` | Confidence from conservation component availability |

---

## Reproducibility

All scores can be reproduced from:
1. The integrated field summary CSV
2. The `app/metrics.py` source code
3. The weight configuration in `config/metric_weights.yaml`
4. Crop parameters in `config/crop_parameters.yaml`

Run `scripts/calculate_metrics.py --input <field_summary.csv> --output <output.csv>` to recompute.
