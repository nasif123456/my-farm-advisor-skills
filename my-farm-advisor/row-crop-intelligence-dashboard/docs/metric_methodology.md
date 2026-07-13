# Metric Methodology

## Field Intelligence Score (FIS)

The primary integrated metric for comparing field-level performance.

**Formula:**
```
FIS = 0.40 × Crop Health + 0.30 × Soil Condition + 0.20 × Weather Suitability + 0.10 × Stability
```

**Interpretation:**
- 75–100: Strong overall field condition
- 60–74: Generally favourable
- 45–59: Moderate concern (Elevated risk)
- Below 45: Priority for investigation (High risk)

**Limitations:**
- Scores are relative to the fields and data included in the dashboard.
- They do not establish cause and effect.
- Weightings are configurable via `config/metric_weights.yaml`.

---

## Soil Health Score

Indicates overall soil condition based on available SSURGO/NRCS variables.

**Components:**
| Component | Weight | Source | Normalization |
|-----------|--------|--------|---------------|
| Organic Matter | 0.25 | SSURGO `om_r` | OM% / 10 × 100 (clipped 0-100) |
| pH Suitability | 0.20 | SSURGO `ph1to1h2o_r` | 100 − \|pH − 6.5\| × 40 (clipped 0-100) |
| Available Water Capacity | 0.20 | SSURGO `awc_r` | AWC / 10 × 100 (clipped 0-100) |
| Drainage Class | 0.20 | SSURGO `drainagecl` | Mapped to score: Well drained=90, Moderately well=75, Somewhat poor=55, Poor=40, Very poor=30 |
| Slope/Erosion | 0.15 | SSURGO erosion risk | Low=100, Moderate=50, High=0 |

**Interpretation:**
- 80–100: Strong soil condition
- 60–79: Generally suitable
- 40–59: Moderate limitation
- Below 40: Higher soil-management priority

---

## Crop Stress Indicator

Identifies fields with apparent vegetation stress based on NDVI patterns.

**Formula:**
```
Crop Stress = 0.50 × Low NDVI + 0.30 × Instability + 0.20 × Early Decline
```

Where:
- **Low NDVI**: 100 − NDVI Score (lower NDVI = more stress)
- **Instability**: 100 − Stability Score (higher variability = more stress)
- **Early Decline**: Approximated from Low NDVI component

**Interpretation:**
- 0–35: Low apparent stress
- 36–65: Moderate apparent stress
- 66–100: High apparent stress

**Important:** NDVI alone cannot identify the specific cause of stress. Results indicate relative patterns, not diagnoses.

---

## Weather Suitability Score

Compares weather conditions across fields for the selected season.

Weather variables are normalised to a 0-100 scale using min-max normalisation:
- Total precipitation
- Cumulative growing degree days (GDD)
- Dry day count (inverted — fewer dry days = higher score)

**GDD Calculation:**
```
GDD = max(0, (Tmax + Tmin) / 2 − Tbase)
```

Default base temperature: 10°C (suitable for corn and soybeans).

---

## Conservation Priority Score

Identifies fields that may benefit from conservation or management attention.

**Formula:**
```
Conservation Priority = 0.35 × Soil Limitation + 0.30 × Crop Stress + 0.20 × Erosion Risk + 0.15 × Weather Exposure
```

**Interpretation:**
- 0–35: Routine monitoring
- 36–65: Monitor — may benefit from assessment
- 66–100: Priority — consider management intervention

---

## Stability Score

Measures consistency of crop health across the season.

Based on the ratio of peak NDVI (95th percentile) to mean NDVI. A higher ratio indicates more variation (potentially from stress events). When direct peak NDVI data is unavailable, mean NDVI is used as a proxy.

---

## Data Sources

| Data | Source | Resolution | Variables Used |
|------|--------|------------|----------------|
| NDVI | Sentinel-2 (via Planetary Computer) | 10m | Mean NDVI, Peak 95th %ile NDVI |
| Weather | NASA POWER | ~0.5° grid | Precipitation, Tmin, Tmax |
| Soil | SSURGO / NRCS | 1:12,000–1:63,360 | OM, pH, AWC, drainage, erosion |
| Boundaries | OpenStreetMap | Field-level | Geometry, area |
| CDL | USDA NASS | 30m | Crop type per year |

---

## Reproducibility

All scores can be reproduced from:
1. The integrated field summary CSV
2. The `app/metrics.py` source code
3. The weight configuration in `config/metric_weights.yaml`

Run `scripts/calculate_metrics.py --input <field_summary.csv> --output <output.csv>` to recompute.
