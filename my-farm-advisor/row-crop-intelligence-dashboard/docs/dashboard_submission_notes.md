# Dashboard Submission Notes

## Project Overview

This project delivers a grower-level Row Crop Intelligence Dashboard that combines geospatial, environmental, crop-health, and soil-condition analysis into one portfolio-ready agricultural decision-support product.

The dashboard is designed for farmers, agronomists, and agri-business analysts who need to compare field performance, identify risk patterns, and understand how weather, vegetation signals, and soil properties vary across a farm operation.

## Dashboard Story

The dashboard tells one integrated field-intelligence story:

- which fields are performing well
- which fields need closer inspection
- how vegetation condition varies across row-crop fields
- how soil screening limitations differ between fields
- how weather and climate context may influence observed outcomes
- how non-row-crop reference areas compare to production fields

The result is meant to function as a practical decision-support tool rather than a disconnected collection of plots.

## Integrated Dataset Description

The dashboard integrates field-level data from multiple agricultural sources and processing steps:

- field boundaries from farm geometry files
- weather and climate summaries from NASA POWER-derived tables
- NDVI and vegetation-condition metrics from prior imagery workflows
- SSURGO/NRCS soil properties and derived soil screening components
- derived field-level metrics including:
  - Field Intelligence Score (FIS)
  - Row-Crop FIS
  - Crop Condition Score
  - Apparent Stress
  - Soil Condition Screening Score
  - Conservation Priority

These datasets are cleaned, joined, and exported into dashboard-ready runtime outputs before HTML rendering.

## Dashboard Sections

### 1. Farm Overview

- KPI summary cards
- compact priority summary
- field, crop, and risk filters
- geospatial field map
- row-crop FIS ranking
- collapsed reference-area section

### 2. Crop Health & Field Variability

- Crop Condition / Apparent Stress toggle
- selected-field summary for single-field inspection
- field comparison table

### 3. Weather & Climate Intelligence

- GDD and precipitation chart
- weather suitability context
- kept collapsed by default to reduce noise in the main decision flow

### 4. Soil Condition Screening & Sustainability

- Soil Condition Screening Score chart
- soil limitation summary
- expandable field-level soil detail table

### 5. Data Provenance & Integrity

- integrity notes
- available-data table

### 6. Methodology & Limitations

- metric definitions
- assumptions and analytical limitations

## Analytical Interpretation

The dashboard supports several high-level interpretations:

- Row-crop fields can be ranked consistently using a transparent integrated score rather than raw NDVI alone.
- Apparent stress and Crop Condition together help identify fields whose vegetation signal deserves follow-up scouting.
- Soil Condition Screening Scores help explain why some fields may be more resilient or more constrained.
- Weather and GDD context help frame the environmental setting around observed field performance.
- Reference areas such as forest or pasture are separated from row-crop comparisons to avoid misleading direct comparisons while still preserving useful contextual vegetation information.

In practice, the dashboard helps answer questions such as:

- Which fields deserve scouting first?
- Which fields appear strongest relative to peers?
- Are weak-performing fields associated more with crop-health signals, soil constraints, or both?
- How much variability exists across the grower’s fields?

## Variables That Matter Most

The most decision-relevant variables in this dashboard are:

- Crop Condition Score
- Apparent Stress
- Row-Crop FIS
- Soil Condition Screening Score
- Conservation Priority
- weather suitability and seasonal context

These variables matter because they connect observed field performance to actionable follow-up steps such as scouting, soil testing, prioritised monitoring, or deeper agronomic review.

## Reproducibility

The dashboard is generated from a reusable skill in `my-farm-advisor-skills`, not from manual notebook-only steps.

Primary reproducible steps include:

1. prepare integrated field-level dashboard data
2. compute derived metrics
3. render a self-contained HTML dashboard

The output location is:

```text
<runtime-dir>/dashboard_outputs/<grower-id>/dashboard.html
```

## AI Use Summary

AI tools were used to support:

- code debugging
- visualization refinement
- dashboard interaction design
- geospatial workflow explanation
- documentation drafting and cleanup
- test-case expansion

All code, metrics, and written interpretation were reviewed and verified manually before final output.

See also:

- `docs/ai_use.md`
- `docs/metric_methodology.md`

## Limitations

- Scores are relative to the fields and data available in the dashboard.
- Remote-sensing indicators cannot diagnose the cause of stress directly.
- Soil screening is not a substitute for field soil sampling or full soil health testing.
- Weather data is gridded, not from an on-farm station.
- Missing or sparse data can reduce analytical confidence.

## Final Deliverable Path

Example generated dashboard used in final validation:

```text
/home/coder/my-farm-advisor-runtime/dashboard_outputs/nebraska-grower/dashboard.html
```
