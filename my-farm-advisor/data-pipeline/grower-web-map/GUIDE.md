---
name: grower-web-map
description: Generate a lightweight interactive Leaflet web map per grower from pipeline field boundaries. Displays field polygons on an OpenStreetMap basemap with click-to-inspect metadata and a sidebar field list.
version: 1.0.0
author: Boreal Bytes
tags: [web-map, visualization, leaflet, geospatial, interactive, grower]
---

# Workflow: grower-web-map

## Description

Generate a self-contained interactive HTML web map for each grower in the My Farm Advisor data-pipeline runtime. The map reads the actual downloaded field polygon boundaries from the pipeline output and renders them on an OpenStreetMap basemap using Leaflet.js.

**Key Features:**

- **Self-contained**: Single HTML file per grower with all field data embedded
- **Dual basemaps**: Satellite (ESRI World Imagery) and Street Map (OSM) with layer toggle
- **Field polygons**: Rendered from the actual pipeline boundary GeoJSON, colored by farm
- **Hover highlight**: Fields highlight on mouse hover (thicker border, layered above)
- **Click to inspect**: Popups with grower, farm, field name, area, county, SSURGO soil data (dominant type, drainage, OM%, pH), 5-year weather averages (temperature, precipitation), and peak NDVI (corn, soybean)
- **Farm legend**: Color swatches with field count and acreage per farm
- **Field list sidebar**: Click a field name to zoom directly to that field
- **Total acreage**: Summary shown in sidebar header
- **Auto-fit**: Map automatically frames all fields on load
- **Lightweight**: Output under 20KB for typical farms

## When to Use

- **Grower review**: Share an interactive map of all fields with a grower
- **Field inspection**: Quickly locate and inspect field boundaries
- **Reporting**: Embed or attach grower-level maps in reports

## Prerequisites

- Data pipeline runtime must be installed with at least one grower
- Runtime Python venv with `geopandas` installed (satisfied by data-pipeline `requirements.txt`)

## Quick Start

Generate maps for all growers:

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/generate_grower_map.py
```

Open a generated map:

```bash
open "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/illinois-grower/derived/maps/grower_map.html"
```

## Single Grower

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/generate_grower_map.py \
  --grower-slug illinois-grower
```

## Output Structure

```
growers/
  <grower-slug>/
    derived/
      maps/
        grower_map.html
```

## Map Controls

- **Zoom in/out**: Mouse scroll, pinch, or +/- buttons
- **Pan**: Click and drag
- **Inspect field**: Click any field polygon — popup shows field, farm, grower, soil (dominant type, drainage, OM%, pH), weather (5-year avg temp, annual precip), and peak NDVI
- **Hover**: Hover over a field to highlight it with a thicker border
- **Zoom to field**: Click a field name in the sidebar list
- **Basemap**: Satellite (default) or Street Map — toggle in the top-right layer control
- **Legend**: Farm color swatches with field count and acreage in sidebar
