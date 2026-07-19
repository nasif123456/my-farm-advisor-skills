# Provenance — Row Crop Intelligence Dashboard

| Field | Value |
|-------|-------|
| Skill Name | row-crop-intelligence-dashboard |
| Repository | borealBytes/my-farm-advisor-skills |
| Created | 2026-07-13 |
| Branch | assignment-4 |
| Author | nasif123456 |
| Upstream Source | my-farm-advisor-skills |
| License | MIT |

## Dependencies

- pandas
- geopandas
- numpy
- plotly.js (vendored)
- shapely

## Data Sources

- Field boundaries: OpenStreetMap via field-boundaries skill
- NDVI: Sentinel-2 / Landsat via satellite-imagery skill
- Weather: NASA POWER via nasa-power-weather skill
- Soil: SSURGO / NRCS via ssurgo-soil skill
- CDL: USDA NASS Cropland Data Layer
