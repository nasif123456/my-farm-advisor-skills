# Row Crop Intelligence Dashboard

Grower-level agricultural analytics dashboard that integrates field boundaries, crop health, weather, soil condition, and field-priority metrics into one self-contained HTML report.

This skill is the reusable dashboard builder for the final project. It works at the grower level, so one run can analyze all fields for a selected grower and produce one unified decision-support dashboard.

## What The Dashboard Covers

- KPI summary for total fields, row-crop fields, other fields, acreage, and condition metrics
- Interactive field comparison filters
- Geospatial field-boundary map colored by risk category
- Row-crop Field Intelligence Score ranking
- Crop Condition and Apparent Stress comparisons
- Weather and climate context using rainfall, temperature, and GDD-derived summaries
- Soil Condition Screening Score chart and field-level soil details
- Reference-area summaries for non-row-crop fields such as forest or pasture

## Final Project Alignment

This dashboard satisfies the core final-project dashboard requirements:

- at least 2 exploratory visualizations
- at least 1 geospatial map
- at least 1 weather or climate visualization
- at least 1 soil health or sustainability metric
- integrated field-level agricultural dataset
- written interpretation embedded in dashboard summaries and supplementary docs

## Runtime Inputs

The dashboard is built from prepared grower-level outputs in the runtime workspace. The generator expects these files under the grower dashboard-output folder:

- `field_summary.csv`
- `field_boundaries.geojson`
- `weather_timeseries.csv` when available
- `soil_summary.csv` when available
- `interpretations.json` when available
- `dashboard_metadata.json` when available

These files are created by the dashboard prep and metric pipeline in this skill tree.

## How To Run

From the skill root:

```bash
python scripts/generate_dashboard.py \
  --grower-id <grower_id> \
  --runtime-dir /absolute/path/to/my-farm-advisor-runtime \
  --year 2024
```

Example:

```bash
python scripts/generate_dashboard.py \
  --grower-id nebraska-grower \
  --runtime-dir /home/coder/my-farm-advisor-runtime \
  --year 2024
```

## Where The Dashboard Is Written

Generated dashboard HTML is written to:

```text
<runtime-dir>/dashboard_outputs/<grower-id>/dashboard.html
```

Example:

```text
/home/coder/my-farm-advisor-runtime/dashboard_outputs/nebraska-grower/dashboard.html
```

Other dashboard-supporting files in the same folder typically include:

- `field_summary.csv`
- `field_boundaries.geojson`
- `dashboard_metadata.json`
- `interpretations.json`
- `weather_timeseries.csv`
- `soil_summary.csv`

## Dependencies

Install Python dependencies from:

```bash
pip install -r requirements.txt
```

Primary libraries used include:

- `pandas`
- `geopandas`
- `numpy`
- `shapely`
- `plotly` (via CDN in the generated HTML)

## Repository Structure

```text
row-crop-intelligence-dashboard/
├── SKILL.md
├── INDEX.md
├── AGENTS.md
├── README.md
├── PROVENANCE.md
├── requirements.txt
├── config/
├── scripts/
├── app/
├── tests/
└── docs/
```

## Key Files

- `scripts/generate_dashboard.py` - CLI entrypoint for final HTML generation
- `scripts/prepare_dashboard_data.py` - exports dashboard-ready integrated data
- `app/metrics.py` - crop health, soil, weather, and intelligence metrics
- `app/renderer.py` - self-contained HTML dashboard renderer
- `docs/metric_methodology.md` - metric definitions and formulas
- `docs/ai_use.md` - AI usage and verification notes
- `docs/dashboard_submission_notes.md` - final project overview and interpretation

## Testing

Run the dashboard test suite with:

```bash
python -m pytest tests -q
```

The renderer tests validate dashboard structure, interactive output assumptions, and preservation of analytical values in the embedded field dataset.

## Notes

- The dashboard is designed as a decision-support tool for farmers, agronomists, and agri-business stakeholders.
- Analytical values are preserved from prepared field-level outputs; dashboard changes should only alter presentation and interaction unless explicitly requested.
- Generated runtime outputs stay outside Git. Only source code, tests, and documentation belong in this repository.
