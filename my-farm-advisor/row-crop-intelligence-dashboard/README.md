# Row Crop Intelligence Dashboard

A grower-level field intelligence dashboard that integrates field boundaries, crop health (NDVI), weather, and soil data into a self-contained HTML decision-support tool.

## Features

- **Grower Overview** — KPI cards, field rankings, and combined field summary
- **Spatial Field Map** — Field boundaries colored by selectable metrics with interactive tooltips
- **Crop Health Analysis** — NDVI time-series comparison across fields, crop stress indicators
- **Weather & GDD Timeline** — Shared-axis precipitation, temperature, and growing degree day charts
- **Soil & Sustainability** — SSURGO soil properties table, Soil Health Score, Conservation Priority
- **Field Priority** — Combined comparison table with risk classification and automated interpretation
- **Field Intelligence Score** — Integrated metric combining crop health, soil condition, weather suitability, and stability

## Usage

```bash
python scripts/generate_dashboard.py \
  --grower-id <grower_id> \
  --runtime-dir ~/my-farm-advisor-runtime \
  --year 2024
```

Open the generated HTML file in any browser.

## Skill Structure

```
row-crop-intelligence-dashboard/
├── SKILL.md              # Skill entrypoint
├── INDEX.md              # Workflow navigation
├── AGENTS.md             # Agent instructions
├── README.md             # This file
├── PROVENANCE.md         # Source record
├── requirements.txt      # Python dependencies
├── config/               # Weight and crop parameter configuration
├── scripts/              # CLI pipeline scripts
├── app/                  # Dashboard application modules
├── tests/                # Test suite
└── docs/                 # Methodology and documentation
```
