#!/usr/bin/env python3
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data_loader import DashboardData
from app.renderer import render_dashboard


def _make_dashboard_data() -> DashboardData:
    rows = [
        {
            "field_id": "osm-1",
            "area_acres": 75,
            "crop_name": "Corn",
            "mean_ndvi": 0.62,
            "ndvi_condition_score": 90,
            "stability_score": 50,
            "soil_condition_screening_score": 82,
            "soil_condition_screening_category": "Strong screening condition",
            "row_crop_fis_score": 88,
            "row_crop_fis_rank": 1,
            "weather_suitability_score": 70,
            "fis_score": 88,
            "crop_stress_apparent": 20,
            "conservation_priority_score": 30,
            "risk_category": "Low",
            "priority_category": "Routine",
            "stress_category": "Low",
            "total_precipitation_mm": 500,
            "cumulative_gdd": 1200,
            "organic_matter_pct": 4.0,
            "soil_ph": 6.8,
            "available_water_capacity_in": 4.0,
            "cec_meq100g": 18,
            "om_score": 80,
            "ph_suitability_score": 90,
            "awc_score": 85,
            "drainage_score": 45,
            "cec_score": 70,
            "drainage_class": "Poorly drained",
            "soil_components_available": "all",
            "soil_components_missing": "none",
            "soil_score_confidence": "High",
            "conservation_confidence": "High",
            "fis_confidence": "High",
            "valid_ndvi_observations": 5,
            "om_depth_cm": 30,
            "ph_depth_cm": 30,
            "cec_depth_cm": 30,
            "awc_profile_depth_cm": 100,
            "dominant_mapunit_name": "MU1",
            "erosion_risk": "moderate",
            "ssurgo_coverage_pct": 90,
        },
        {
            "field_id": "osm-2",
            "area_acres": 65,
            "crop_name": "Corn",
            "mean_ndvi": 0.35,
            "ndvi_condition_score": 0,
            "stability_score": 50,
            "soil_condition_screening_score": 60,
            "soil_condition_screening_category": "Moderate screening condition",
            "row_crop_fis_score": 48,
            "row_crop_fis_rank": 3,
            "weather_suitability_score": 60,
            "fis_score": 48,
            "crop_stress_apparent": 70,
            "conservation_priority_score": 55,
            "risk_category": "Elevated",
            "priority_category": "Priority",
            "stress_category": "High",
            "total_precipitation_mm": 480,
            "cumulative_gdd": 1180,
            "organic_matter_pct": 2.0,
            "soil_ph": 5.7,
            "available_water_capacity_in": 2.8,
            "cec_meq100g": 10,
            "om_score": 35,
            "ph_suitability_score": 35,
            "awc_score": 30,
            "drainage_score": 30,
            "cec_score": 35,
            "drainage_class": "Very poorly drained",
            "soil_components_available": "all",
            "soil_components_missing": "none",
            "soil_score_confidence": "Moderate",
            "conservation_confidence": "Moderate",
            "fis_confidence": "Moderate",
            "valid_ndvi_observations": 4,
            "om_depth_cm": 30,
            "ph_depth_cm": 30,
            "cec_depth_cm": 30,
            "awc_profile_depth_cm": 100,
            "dominant_mapunit_name": "MU2",
            "erosion_risk": "high",
            "ssurgo_coverage_pct": 88,
        },
        {
            "field_id": "osm-3",
            "area_acres": 50,
            "crop_name": "Soybeans",
            "mean_ndvi": 0.55,
            "ndvi_condition_score": 75,
            "stability_score": 50,
            "soil_condition_screening_score": 78,
            "soil_condition_screening_category": "Strong screening condition",
            "row_crop_fis_score": 72,
            "row_crop_fis_rank": 2,
            "weather_suitability_score": 65,
            "fis_score": 72,
            "crop_stress_apparent": 35,
            "conservation_priority_score": 40,
            "risk_category": "Moderate",
            "priority_category": "Monitor",
            "stress_category": "Moderate",
            "total_precipitation_mm": 490,
            "cumulative_gdd": 1190,
            "organic_matter_pct": 3.5,
            "soil_ph": 6.5,
            "available_water_capacity_in": 3.8,
            "cec_meq100g": 16,
            "om_score": 70,
            "ph_suitability_score": 85,
            "awc_score": 75,
            "drainage_score": 50,
            "cec_score": 68,
            "drainage_class": "Somewhat poorly drained",
            "soil_components_available": "all",
            "soil_components_missing": "none",
            "soil_score_confidence": "High",
            "conservation_confidence": "High",
            "fis_confidence": "High",
            "valid_ndvi_observations": 4,
            "om_depth_cm": 30,
            "ph_depth_cm": 30,
            "cec_depth_cm": 30,
            "awc_profile_depth_cm": 100,
            "dominant_mapunit_name": "MU3",
            "erosion_risk": "moderate",
            "ssurgo_coverage_pct": 91,
        },
        {
            "field_id": "osm-4",
            "area_acres": 30,
            "crop_name": "Forest",
            "mean_ndvi": 0.40,
            "ndvi_condition_score": 60,
            "stability_score": 50,
            "soil_condition_screening_score": 80,
            "soil_condition_screening_category": "Strong screening condition",
            "row_crop_fis_score": None,
            "row_crop_fis_rank": None,
            "weather_suitability_score": 55,
            "fis_score": 58,
            "crop_stress_apparent": 30,
            "conservation_priority_score": 35,
            "risk_category": "Moderate",
            "priority_category": "Routine",
            "stress_category": "Low",
            "total_precipitation_mm": 470,
            "cumulative_gdd": 1170,
            "organic_matter_pct": 4.2,
            "soil_ph": 6.7,
            "available_water_capacity_in": 4.1,
            "cec_meq100g": 17,
            "om_score": 82,
            "ph_suitability_score": 88,
            "awc_score": 84,
            "drainage_score": 52,
            "cec_score": 72,
            "drainage_class": "Well drained",
            "soil_components_available": "all",
            "soil_components_missing": "none",
            "soil_score_confidence": "High",
            "conservation_confidence": "High",
            "fis_confidence": "Moderate",
            "valid_ndvi_observations": 3,
            "om_depth_cm": 30,
            "ph_depth_cm": 30,
            "cec_depth_cm": 30,
            "awc_profile_depth_cm": 100,
            "dominant_mapunit_name": "MU4",
            "erosion_risk": "low",
            "ssurgo_coverage_pct": 87,
        },
        {
            "field_id": "osm-5",
            "area_acres": 20,
            "crop_name": "Grass/Pasture",
            "mean_ndvi": 0.45,
            "ndvi_condition_score": 65,
            "stability_score": 50,
            "soil_condition_screening_score": 77,
            "soil_condition_screening_category": "Strong screening condition",
            "row_crop_fis_score": None,
            "row_crop_fis_rank": None,
            "weather_suitability_score": 58,
            "fis_score": 61,
            "crop_stress_apparent": 25,
            "conservation_priority_score": 32,
            "risk_category": "Low",
            "priority_category": "Routine",
            "stress_category": "Low",
            "total_precipitation_mm": 465,
            "cumulative_gdd": 1160,
            "organic_matter_pct": 4.1,
            "soil_ph": 6.6,
            "available_water_capacity_in": 4.0,
            "cec_meq100g": 16,
            "om_score": 80,
            "ph_suitability_score": 86,
            "awc_score": 82,
            "drainage_score": 50,
            "cec_score": 70,
            "drainage_class": "Well drained",
            "soil_components_available": "all",
            "soil_components_missing": "none",
            "soil_score_confidence": "High",
            "conservation_confidence": "High",
            "fis_confidence": "Moderate",
            "valid_ndvi_observations": 3,
            "om_depth_cm": 30,
            "ph_depth_cm": 30,
            "cec_depth_cm": 30,
            "awc_profile_depth_cm": 100,
            "dominant_mapunit_name": "MU5",
            "erosion_risk": "low",
            "ssurgo_coverage_pct": 85,
        },
    ]
    df = pd.DataFrame(rows)
    polys = [
        Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
        Polygon([(2, 0), (3, 0), (3, 1), (2, 1)]),
        Polygon([(4, 0), (5, 0), (5, 1), (4, 1)]),
        Polygon([(6, 0), (7, 0), (7, 1), (6, 1)]),
        Polygon([(8, 0), (9, 0), (9, 1), (8, 1)]),
    ]
    gdf = gpd.GeoDataFrame({"field_id": df["field_id"], "geometry": polys}, geometry="geometry", crs="EPSG:4326")
    weather = pd.DataFrame(
        {
            "field_id": ["osm-1", "osm-2", "osm-3", "osm-4", "osm-5"],
            "date": ["2024-06-01"] * 5,
            "lat": [42.0] * 5,
            "lon": [-93.0] * 5,
            "T2M_MAX": [29, 30, 28, 27, 26],
            "T2M_MIN": [18, 19, 17, 16, 15],
            "PRECTOTCORR": [3, 4, 5, 2, 1],
        }
    )
    return DashboardData(
        field_summary=df,
        field_boundaries=gdf,
        weather_timeseries=weather,
        metadata={"grower_id": "test-grower", "year": 2024, "field_count": len(df)},
    )


def _render_html(tmp_path: Path) -> str:
    out = tmp_path / "dashboard.html"
    render_dashboard(_make_dashboard_data(), out)
    return out.read_text()


def _fields_data_from_html(html: str) -> list[dict]:
    m = re.search(r"var fieldsData = (\[.*?\]);", html, re.S)
    assert m, "fieldsData JSON not found"
    return json.loads(m.group(1))


def _js_json_var(html: str, var_name: str):
    m = re.search(rf"var {var_name} = (\[.*?\]|\{{.*?\}});", html, re.S)
    assert m, f"{var_name} JSON not found"
    return json.loads(m.group(1))


def test_old_overview_and_key_findings_removed(tmp_path: Path):
    html = _render_html(tmp_path)
    assert "Key Findings" not in html
    assert "Integrated field intelligence combining crop health" not in html
    assert "grower_summary" not in html


def test_short_field_filter_labels_and_crop_risk_filters_present(tmp_path: Path):
    html = _render_html(tmp_path)
    assert 'id="fieldFilterButton"' in html
    assert 'id="fieldFilterMenu"' in html
    assert 'id="fieldFilterOptions"' in html
    assert 'Field 1 · Corn' in html
    assert 'function renderFieldCheckboxMenu()' in html
    assert 'fieldOptionLabel(f)' in html
    assert '<select id="cropFilter"' in html
    assert '<select id="riskFilter"' in html
    assert 'crops.add(f.crop)' in html
    assert 'risks.add(f.risk_category)' in html


def test_reference_and_technical_sections_collapsed(tmp_path: Path):
    html = _render_html(tmp_path)
    assert 'reference area(s):' in html or 'Reference land-cover areas' in html
    assert 'id="referenceDetails"' in html
    assert 'View field soil details' in html
    assert 'Weather & Climate Intelligence' in html
    assert 'Data Provenance & Integrity' in html
    assert 'Methodology & Limitations' in html


def test_priority_tag_css_and_no_field_field_text(tmp_path: Path):
    html = _render_html(tmp_path)
    assert '.tag-priority' in html
    assert 'Field Field' not in html


def test_table_rows_have_filter_data_attributes(tmp_path: Path):
    html = _render_html(tmp_path)
    assert 'data-field-id="osm-1"' in html
    assert 'data-crop="Corn"' in html
    assert 'data-risk="Low"' in html
    assert 'data-soil-field-id="osm-1"' in html


def test_map_is_in_farm_overview_and_separate_map_section_removed(tmp_path: Path):
    html = _render_html(tmp_path)
    farm_idx = html.index('<h2>Farm Overview</h2>')
    map_idx = html.index('id="mapChart"')
    rank_idx = html.index('id="rankingChart"')
    crop_idx = html.index('<h2>Crop Health & Field Variability</h2>')
    assert farm_idx < map_idx < crop_idx
    assert farm_idx < rank_idx < crop_idx
    assert '<h2>Spatial Field Map</h2>' not in html


def test_renderer_preserves_analytical_values_in_embedded_fields_data(tmp_path: Path):
    html = _render_html(tmp_path)
    data = _fields_data_from_html(html)
    by_id = {f['field_id']: f for f in data}
    assert by_id['osm-1']['fis'] == 88.0
    assert by_id['osm-2']['row_crop_fis_score'] == 48.0
    assert by_id['osm-2']['ndvi_condition_score'] == 0.0
    assert by_id['osm-3']['ndvi_condition_score'] == 75.0
    assert by_id['osm-4']['row_crop_fis_score'] is None
    assert by_id['osm-5']['soil_condition_screening_score'] == 77.0


def test_dashboard_state_and_detail_row_classes_exist(tmp_path: Path):
    html = _render_html(tmp_path)
    assert 'var dashboardState = {' in html
    assert "expandedFieldId" in html
    assert "expandedSoilFieldId" in html
    assert 'class="field-primary' in html
    assert 'class="field-detail"' in html
    assert 'class="soil-primary"' in html
    assert 'class="soil-detail"' in html


def test_filtering_and_reset_keep_detail_rows_closed(tmp_path: Path):
    html = _render_html(tmp_path)
    assert 'function closeAllDetailRows()' in html
    assert "document.querySelectorAll('.field-detail').forEach(function(row) { row.style.display = 'none'; });" in html
    assert "document.querySelectorAll('.soil-detail').forEach(function(row) { row.style.display = 'none'; });" in html
    assert "row.style.display = (match && dashboardState.expandedFieldId === row.getAttribute('data-field-id')) ? 'table-row' : 'none';" in html
    assert "row.style.display = (match && dashboardState.expandedSoilFieldId === row.getAttribute('data-soil-field-id')) ? 'table-row' : 'none';" in html


def test_zero_value_is_preserved_and_low_condition_logic_uses_nullish_checks(tmp_path: Path):
    html = _render_html(tmp_path)
    assert 'botF.ndvi_condition_score ?? 100' in html
    assert 'botF.ndvi_condition_score || 100' not in html
    data = _fields_data_from_html(html)
    by_id = {f['field_id']: f for f in data}
    assert by_id['osm-2']['ndvi_condition_score'] == 0.0


def test_default_mean_crop_condition_uses_row_crop_fields_only(tmp_path: Path):
    html = _render_html(tmp_path)
    assert '>55/100</div><div class="label">Mean Crop Condition</div>' in html


def test_initial_crop_health_chart_excludes_reference_fields(tmp_path: Path):
    html = _render_html(tmp_path)
    ndvi = _js_json_var(html, 'ndviCondTrace')
    assert len(ndvi[0]['x']) == 3
    labels = ndvi[0]['y']
    assert all('Forest' not in label and 'Grass/Pasture' not in label for label in labels)


def test_no_results_clears_weather_and_gdd_and_reset_restores_them(tmp_path: Path):
    html = _render_html(tmp_path)
    assert "Plotly.react('gddPrecipChart', [], {height: 100});" in html
    assert "Plotly.react('weatherChart', [], {height: 100});" in html
    assert "Plotly.react('gddPrecipChart', gddPrecipTrace, gddPrecipLayout);" in html
    assert "Plotly.react('weatherChart', weatherTrace, weatherLayout);" in html


def test_selected_map_field_emphasis_and_risk_order(tmp_path: Path):
    html = _render_html(tmp_path)
    assert 'out.line.width = 4;' in html
    assert "out.fillcolor = out.fillcolor.slice(0, 7) + '90';" in html
    assert "var riskOrder = ['Low', 'Moderate', 'Elevated', 'High'];" in html


def test_reference_and_soil_summaries_are_filter_aware(tmp_path: Path):
    html = _render_html(tmp_path)
    assert 'function buildReferenceSummary(filtered)' in html
    assert 'function buildSoilSummary(filtered)' in html
    assert 'id="referenceSummary"' in html
    assert 'id="soilSummary"' in html


def test_soil_table_uses_short_labels_and_no_duplicate_tag_elevated(tmp_path: Path):
    html = _render_html(tmp_path)
    assert 'Field 1<br><span style="font-size:var(--font-sm);color:var(--muted);">Corn</span>' in html
    assert html.count('.tag-elevated') == 1
