---
name: row-crop-intelligence-dashboard
description: >
  Grower-level row crop intelligence dashboard that integrates field boundaries,
  NDVI, weather, soil, and composite metrics into a self-contained HTML decision-support
  report for farmers, agronomists, and precision agriculture analysts.
---

# Row Crop Intelligence Dashboard

This skill creates a grower-level interactive dashboard that compares all fields
belonging to a selected grower. It answers five practical questions:

1. Which fields are performing well?
2. Which fields show signs of crop stress or high variability?
3. How have rainfall, temperature, and growing degree days affected crop development?
4. Which soil properties may explain differences between fields?
5. Which fields should receive further inspection, soil testing, or management attention?

## Entrypoints

- [INDEX.md](INDEX.md) — Workflow navigation
- [README.md](README.md) — Human overview
- [AGENTS.md](AGENTS.md) — Agent operating instructions
- [scripts/generate_dashboard.py](scripts/generate_dashboard.py) — CLI entry point
