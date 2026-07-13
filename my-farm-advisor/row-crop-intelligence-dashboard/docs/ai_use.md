# AI Use Documentation

## How AI Was Used

AI tools supported the development of this dashboard skill in the following ways:

### Planning and Structure
- Drafting the initial project plan and work breakdown structure.
- Organising dashboard sections and identifying data integration points.

### Code Development
- Generating boilerplate for Python scripts (CLI argument parsing, file I/O, data validation).
- Suggesting Plotly.js HTML template structure and JavaScript chart configurations.
- Debugging Python errors (import resolution, path handling, data type issues).
- Refactoring metric calculation logic into modular functions.

### Documentation
- Improving README clarity and structure.
- Drafting metric methodology explanations.
- Generating test scenario suggestions.

### Quality Review
- Suggesting additional edge cases for testing.
- Reviewing geospatial processing logic for coordinate system issues.
- Identifying missing data handling gaps.

## Verification Process

All AI-generated code, text, and suggestions were reviewed and verified as follows:

### Code Verification
- Every Python script was run and tested against the Iowa grower dataset.
- Metric calculations were manually verified against source data.
- Dashboard HTML output was inspected for correct rendering.
- Edge cases (missing data, invalid grower IDs, empty fields) were tested.

### Data Verification
- Field boundary geometry was validated for CRS correctness.
- Weather data units and ranges were confirmed against NASA POWER documentation.
- SSURGO soil property units were verified against NRCS metadata.
- NDVI values were confirmed to be in the valid [-1, 1] range.

### Analytical Verification
- One NDVI mean was manually traced from scene TIFF to dashboard output.
- One GDD calculation was verified by hand.
- One Soil Health Score was recomputed manually from source data.

### Documentation Review
- Methodology descriptions were reviewed for accuracy.
- Limitations were verified against actual data coverage.
- AI-generated text was edited for factual correctness and clarity.

## Responsibility

The final analytical decisions, code quality, data handling, and documentation accuracy remain the responsibility of the project author. AI tools were used as an accelerator and assistant, not as a replacement for domain knowledge or analytical judgement.
