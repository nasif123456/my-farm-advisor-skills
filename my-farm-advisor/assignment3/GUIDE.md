# Assignment 3: Field-Year Dashboard Prototype

One field-year dashboard comparing NDVI and weather across a shared timeline.

## Selected Prototype

| Attribute | Value |
|---|---|
| Grower | Iowa grower (`iowa-grower`) |
| Farm | Northern Iowa Farm (`iowa-grower-iowa`), Kossuth County |
| Field | `osm-1360386537` (148 ac) |
| Year | 2024 |
| CDL Crop | Corn (96.5% purity) |
| NDVI Observations | 15 (7 Landsat + 8 Sentinel) |
| Weather Completeness | 275/275 days (100%) |

### Why This Field-Year

- Cleanest CDL classification across all growers and years
- Complete weather data with all variables (T2M, TMAX, TMIN, PRECTOTCORR, solar, RH, wind)
- 15 NDVI observations spanning March–November
- Corn season shows a clear NDVI-weather relationship, making the dashboard more instructive
- Perfect 2-year corn/soybean rotation for future multi-year extension

## Input Data

| Source | Format | Runtime Path |
|---|---|---|
| Field boundaries | GeoJSON | `growers/<grower>/farms/<farm>/boundary/field_boundaries.geojson` |
| CDL full composition | CSV | `growers/<grower>/farms/<farm>/derived/tables/<farm>_cdl_<start>_<end>_full_composition.csv` |
| CDL rotation | CSV | `growers/<grower>/farms/<farm>/derived/tables/<farm>_crop_rotation.csv` |
| Weather (NASA POWER) | CSV | `growers/<grower>/farms/<farm>/derived/tables/<farm>_weather_<start>_<end>.csv` |
| Landsat NDVI | GeoTIFF | `fields/<field>/satellite/landsat/<year>/*/landsat*ndvi.tif` |
| Sentinel NDVI | GeoTIFF | `fields/<field>/satellite/sentinel/<year>/*/*ndvi.tif` |

## Workflow

1. **CDL crop identification** — reads the dominant crop for the field-year from the full composition table
2. **NDVI extraction** — reads each Landsat and Sentinel raster via rasterio, computes the mean NDVI per scene
3. **Weather loading** — filters the farm-level weather CSV to the specific field and year
4. **Aligned table** — builds a daily date axis (Mar 1–Nov 30) with weather metrics, GDD calculation, and NDVI values on observation dates only
5. **Event detection** — rule-based detection of heavy rain, heat, cool periods, dry gaps, NDVI dips, green-up, peak, and late-season decline
6. **Dashboard plot** — 3-panel static figure (NDVI / Temperature / Precip+GDD) with event annotations
7. **Outputs** — aligned CSV, dashboard PNG, coverage summary markdown

### GDD Calculation

- Corn base: 10°C (50°F) with upper cap at 30°C
- Soybean base: 10°C (50°F) with upper cap at 30°C
- Formula: `max(0, (min(TMAX, 30) + max(TMIN, base)) / 2 - base)`

## Output

```
shared/assignment3/derived/
├── tables/
│   └── field_year_aligned_<grower>_<field>_<year>.csv
├── reports/
│   ├── field_year_dashboard_<grower>_<field>_<year>.png
│   └── field_year_coverage_<grower>_<field>_<year>.md
```

## Run (Single Year)

```bash
export DATA_PIPELINE_DATA_ROOT=$HOME/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/assignment3/assignment3_main.py \
  --grower-slug iowa-grower \
  --farm-slug iowa-grower-iowa \
  --field-id osm-1360386537 \
  --year 2024
```

## Run (All Years)

```bash
export DATA_PIPELINE_DATA_ROOT=$HOME/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/assignment3/assignment3_main.py \
  --grower-slug iowa-grower \
  --farm-slug iowa-grower-iowa \
  --field-id osm-1360386537 \
  --all-years
```

## Extension to All Years

The `--all-years` flag iterates 2021–2025, generating separate aligned tables, dashboards, and summaries per year. CDL crop is re-identified per year (not assumed constant). The same reusable functions work across all years with no hard-coded values.

## Limitations

- NDVI is extracted as field-mean from the full raster (no crop-masking per pixel)
- Landsat and Sentinel NDVI values are not normalized — plotted separately for transparency
- No interpolation of NDVI between observation dates
- GDD uses CDL-identified crop base temperature; defaults to corn base if crop is unknown
- Only 2 of 10 Iowa fields have pre-computed NDVI summary tables; rasters are read directly
