# Row Crop Intelligence Dashboard — INDEX

## Workflow Areas

| Area | File | Purpose |
|------|------|---------|
| Input Validation | [scripts/validate_inputs.py](scripts/validate_inputs.py) | Check data quality before processing |
| Data Integration | [scripts/integrate_data.py](scripts/integrate_data.py) | Join field, NDVI, weather, and soil data |
| Metric Calculation | [scripts/calculate_metrics.py](scripts/calculate_metrics.py) | Compute composite scores |
| Dashboard Prep | [scripts/prepare_dashboard_data.py](scripts/prepare_dashboard_data.py) | Export dashboard-ready files |
| Dashboard Generation | [scripts/generate_dashboard.py](scripts/generate_dashboard.py) | Build the self-contained HTML dashboard |
| Data Loader | [app/data_loader.py](app/data_loader.py) | Load and validate dashboard input data |
| Metrics | [app/metrics.py](app/metrics.py) | Composite score implementations |
| Interpretations | [app/interpretations.py](app/interpretations.py) | Rule-based field interpretation |
| Renderer | [app/renderer.py](app/renderer.py) | HTML dashboard template and rendering |

## Quick Start

```bash
python scripts/generate_dashboard.py --grower-id <grower_id> --runtime-dir <path>
```
