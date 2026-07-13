# Row Crop Intelligence Dashboard — AGENTS

## Purpose

This skill generates a grower-level row crop intelligence dashboard as a self-contained
HTML file. It integrates field boundaries, NDVI time series, weather data, SSURGO soil
properties, and composite metrics into a single decision-support product.

## Operating Rules

1. All generated outputs go under the runtime workspace, not in this repository.
2. The dashboard must work for any grower, not just one hard-coded example.
3. Composite scores must be transparent and rule-based, not black-box.
4. All interpretations must clearly state limitations and the need for field scouting.
5. CLI parameters should not hard-code file paths specific to one machine.

## Safe Edit Scope

Edits to files in this directory are safe. Edits to shared library files
(`dashboard_generator.py`, `paths.py`, etc.) should be coordinated with the
data-pipeline skill.

## Validation

```bash
python scripts/generate_dashboard.py --grower-id iowa-grower --runtime-dir ~/my-farm-advisor-runtime --year 2024
```

Check that:
- The output HTML contains all 6 dashboard sections
- Grower and field filters work
- Missing data is handled gracefully
- Metric values fall in expected ranges
