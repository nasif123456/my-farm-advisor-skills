#!/usr/bin/env python3
"""HTML dashboard rendering with Plotly.js for the row crop intelligence dashboard."""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import numpy as np

from .data_loader import DashboardData

log = logging.getLogger(__name__)

COLORBLIND_PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]

RISK_COLORS = {"Low": "#2ca02c", "Moderate": "#ff7f0e", "Elevated": "#d62728", "High": "#8b0000"}
PRIORITY_COLORS = {"Routine": "#2ca02c", "Monitor": "#ff7f0e", "Priority": "#d62728"}
CONFIDENCE_COLORS = {"High": "#2ca02c", "Moderate": "#ff7f0e", "Low": "#d62728", "Insufficient data": "#9e9e9e"}


def _color_for_field(idx: int) -> str:
    return COLORBLIND_PALETTE[idx % len(COLORBLIND_PALETTE)]


def _reclassify_risk(fis_score: float | None) -> tuple[str, str, str]:
    """Consistent risk/stress/priority categories matching app/metrics.py."""
    if fis_score is None:
        return "High", "Moderate", "Monitor"
    if fis_score >= 65:
        return "Low", "Low", "Routine"
    elif fis_score >= 50:
        return "Moderate", "Low", "Routine"
    elif fis_score >= 35:
        return "Elevated", "Moderate", "Monitor"
    return "High", "High", "Priority"


def _field_to_geo_polygons(gdf: gpd.GeoDataFrame) -> dict[str, list[list[list[float]]]]:
    """Extract lon/lat polygon rings from a GeoDataFrame (WGS84)."""
    result = {}
    for _, row in gdf.iterrows():
        fid = row["field_id"]
        polygons = []
        geom = row.geometry
        if geom.geom_type == "Polygon":
            coords = [(round(x, 6), round(y, 6)) for x, y in geom.exterior.coords]
            polygons.append([[lon, lat] for lon, lat in coords])
        elif geom.geom_type == "MultiPolygon":
            for poly in geom.geoms:
                coords = [(round(x, 6), round(y, 6)) for x, y in poly.exterior.coords]
                polygons.append([[lon, lat] for lon, lat in coords])
        result[fid] = polygons
    return result


def _map_drainage_score(drainage_class: str) -> int:
    mapping = {
        "Excessively drained": 40, "Somewhat excessively drained": 50,
        "Well drained": 90, "Moderately well drained": 75,
        "Somewhat poorly drained": 55, "Poorly drained": 40, "Very poorly drained": 30,
    }
    return mapping.get(str(drainage_class).strip(), 50)


def _build_field_data(df: pd.DataFrame, gdf: gpd.GeoDataFrame) -> list[dict[str, Any]]:
    geo_polys = _field_to_geo_polygons(gdf) if gdf is not None and not gdf.empty else {}
    fields = []
    for _, row in df.iterrows():
        fid = row["field_id"]
        rc_rank = row.get("row_crop_fis_rank")
        if rc_rank is not None and isinstance(rc_rank, float) and math.isnan(rc_rank):
            rc_rank = None
        f = {
            "field_id": fid,
            "area_acres": row.get("area_acres", 0),
            "crop": row.get("crop_name", ""),
            "mean_ndvi": _safe_float(row.get("mean_ndvi")),
            "ndvi_condition_score": _safe_float(row.get("ndvi_condition_score")),
            "stability_score": _safe_float(row.get("stability_score")),
            "soil_condition_screening_score": _safe_float(row.get("soil_condition_screening_score")),
            "soil_screening_category": str(row.get("soil_condition_screening_category", "")),
            "row_crop_fis_score": _safe_float(row.get("row_crop_fis_score")),
            "row_crop_fis_rank": rc_rank,
            "weather_suitability_score": _safe_float(row.get("weather_suitability_score")),
            "fis": _safe_float(row.get("fis_score")),
            "crop_stress": _safe_float(row.get("crop_stress_apparent")),
            "conservation_priority": _safe_float(row.get("conservation_priority_score")),
            "risk_category": row.get("risk_category", ""),
            "priority_category": row.get("priority_category", ""),
            "stress_category": row.get("stress_category", ""),
            "total_precipitation_mm": _safe_float(row.get("total_precipitation_mm")),
            "cumulative_gdd": _safe_float(row.get("cumulative_gdd")),
            "organic_matter_pct": _safe_float(row.get("organic_matter_pct")),
            "soil_ph": _safe_float(row.get("soil_ph")),
            "available_water_capacity_in": _safe_float(row.get("available_water_capacity_in")),
            "cec_meq100g": _safe_float(row.get("cec_meq100g")),
            "om_score": _safe_float(row.get("om_score")),
            "ph_suitability_score": _safe_float(row.get("ph_suitability_score")),
            "awc_score": _safe_float(row.get("awc_score")),
            "drainage_score": _safe_float(row.get("drainage_score")),
            "cec_score": _safe_float(row.get("cec_score")),
            "drainage_class": row.get("drainage_class", ""),
            "soil_components_available": row.get("soil_components_available", ""),
            "soil_components_missing": row.get("soil_components_missing", ""),
            "soil_score_confidence": row.get("soil_score_confidence", ""),
            "soil_score_method": row.get("soil_score_method", ""),
            "conservation_components_available": row.get("conservation_components_available", ""),
            "conservation_confidence": row.get("conservation_confidence", ""),
            "fis_confidence": row.get("fis_confidence", ""),
            "fis_components_available": row.get("fis_components_available", ""),
            "ndvi_condition_confidence": row.get("ndvi_condition_confidence", ""),
            "valid_ndvi_observations": int(row.get("valid_ndvi_observations", 0)),
            # Traceability columns
            "om_depth_cm": row.get("om_depth_cm"),
            "ph_depth_cm": row.get("ph_depth_cm"),
            "cec_depth_cm": row.get("cec_depth_cm"),
            "awc_profile_depth_cm": row.get("awc_profile_depth_cm"),
            "dominant_mapunit_name": row.get("dominant_mapunit_name"),
            "dominant_mapunit_pct": row.get("dominant_mapunit_pct"),
            "k_factor": _safe_float(row.get("k_factor")),
            "erosion_risk": row.get("erosion_risk"),
            "erosion_evidence_source": row.get("erosion_evidence_source"),
            "ssurgo_coverage_pct": _safe_float(row.get("ssurgo_coverage_pct")),
            "awc_validation_note": row.get("awc_validation_note"),
            "soil_aggregation_method": row.get("soil_aggregation_method"),
            "n_row_crop_fields": row.get("n_row_crop_fields"),
            "n_reference_fields": row.get("n_reference_fields"),
            "row_crop_mean_fis": row.get("row_crop_mean_fis"),
            "geo_polygons": geo_polys.get(fid, []),
        }
        fis = f.get("fis")
        if fis is not None and not f.get("risk_category"):
            risk, stress, pri = _reclassify_risk(fis)
            f["risk_category"] = risk if not f.get("risk_category") else f["risk_category"]
            f["stress_category"] = stress
            f["priority_category"] = pri
        fields.append(f)
    return fields


def _safe_float(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and math.isnan(v)) or (isinstance(v, str) and v == ""):
        return None
    try:
        return round(float(v), 2)
    except (ValueError, TypeError):
        return None


def _make_short_labels(fields_data: list[dict]) -> dict[str, str]:
    sorted_fields = sorted(fields_data, key=lambda f: f.get("row_crop_fis_score") or f.get("fis") or 0, reverse=True)
    return {f["field_id"]: f"Field {i+1}" for i, f in enumerate(sorted_fields)}


def _short_label(labels: dict[str, str], fid: str, crop: str = "") -> str:
    s = labels.get(fid, fid)
    if crop:
        return f"{s} · {crop}"
    return s


def _make_ranking_chart_json(fields_data: list[dict], short_labels: dict[str, str]) -> tuple[str, str, str, str]:
    row_crop = [f for f in fields_data if f.get("row_crop_fis_score") is not None]
    reference = [f for f in fields_data if f.get("row_crop_fis_score") is None]
    empty_trace = json.dumps([], cls=_NumpyEncoder)
    empty_layout = json.dumps({"height": 100}, cls=_NumpyEncoder)

    def _chart(data, title_str) -> tuple[str, str]:
        if not data:
            return empty_trace, empty_layout
        data_sorted = sorted(data, key=lambda f: f.get("fis") or 0, reverse=True)
        labels = [_short_label(short_labels, f["field_id"], f.get("crop", "")) for f in data_sorted]
        values = [f.get("fis") or 0 for f in data_sorted]
        colors = [RISK_COLORS.get(f.get("risk_category", ""), "#7f7f7f") for f in data_sorted]
        trace = {
            "type": "bar",
            "x": values,
            "y": labels,
            "orientation": "h",
            "marker": {"color": colors},
            "textposition": "none",
            "hovertemplate": (
                "<b>%{y}</b><br>"
                "FIS: %{x:.1f}/100<br>"
                "Acreage: %{customdata[0]:.0f} ac<br>"
                "Risk: %{customdata[1]}<br>"
                "Crop condition: %{customdata[2]:.0f}/100<br>"
                "Soil screening: %{customdata[3]:.0f}/100<extra></extra>"
            ),
            "customdata": [[f.get("area_acres", 0), f.get("risk_category", ""),
                           f.get("ndvi_condition_score") or 0, f.get("soil_condition_screening_score") or 0]
                          for f in data_sorted],
        }
        layout = {
            "title": {"text": title_str, "font": {"size": 15}},
            "xaxis": {"title": "Field Intelligence Score (0–100)", "range": [0, 105], "dtick": 10},
            "yaxis": {"title": "", "automargin": True},
            "height": max(200, len(labels) * 36),
            "margin": {"l": 120, "r": 20, "t": 40, "b": 40},
            "bargap": 0.35,
        }
        return json.dumps([trace], cls=_NumpyEncoder), json.dumps(layout, cls=_NumpyEncoder)

    rc_traces, rc_layout = _chart(row_crop, "Row-Crop Field Intelligence Score Ranking")
    ref_traces, ref_layout = _chart(reference, "Reference Land Cover — FIS Summary")
    return rc_traces, rc_layout, ref_traces, ref_layout


def _make_ndvi_chart_json(fields_data: list[dict], short_labels: dict[str, str]) -> tuple[str, str, str]:
    base_fields = [f for f in fields_data if f.get("row_crop_fis_score") is not None] or fields_data
    sorted_fields = sorted(base_fields, key=lambda f: f.get("ndvi_condition_score") or 0, reverse=True)
    labels = []
    cond_vals = []
    stress_vals = []
    for f in sorted_fields:
        labels.append(_short_label(short_labels, f["field_id"], f.get("crop", "")))
        cond_vals.append(f.get("ndvi_condition_score") or 0)
        stress_vals.append(f.get("crop_stress") or 0)

    def _cond_color(v):
        if v >= 65: return "#2e7d32"
        if v >= 50: return "#ff8f00"
        return "#b71c1c"

    def _stress_color(v):
        if v >= 60: return "#b71c1c"
        if v >= 40: return "#ff8f00"
        return "#2e7d32"

    cond_trace = {
        "type": "bar", "x": cond_vals, "y": labels, "orientation": "h",
        "marker": {"color": [_cond_color(v) for v in cond_vals]},
        "textposition": "none",
        "hovertemplate": "<b>%{y}</b><br>NDVI Condition: %{x:.0f}/100<extra></extra>",
    }
    stress_trace = {
        "type": "bar", "x": stress_vals, "y": labels, "orientation": "h",
        "marker": {"color": [_stress_color(v) for v in stress_vals]},
        "textposition": "none",
        "hovertemplate": "<b>%{y}</b><br>Apparent Stress: %{x:.0f}/100<extra></extra>",
    }
    layout = {
        "title": {"text": "Crop Health", "font": {"size": 15}},
        "xaxis": {"title": "Score (0–100)", "range": [0, 105], "dtick": 10},
        "yaxis": {"title": "", "automargin": True},
        "height": max(200, len(labels) * 32),
        "margin": {"l": 120, "r": 20, "t": 40, "b": 40},
        "bargap": 0.35,
    }
    return (json.dumps([cond_trace], cls=_NumpyEncoder),
            json.dumps([stress_trace], cls=_NumpyEncoder),
            json.dumps(layout, cls=_NumpyEncoder))


def _soil_category_color(score: float | None) -> str:
    if score is None:
        return "#9e9e9e"
    if score >= 80:
        return "#1b5e20"
    if score >= 65:
        return "#2e7d32"
    if score >= 50:
        return "#ff8f00"
    if score >= 35:
        return "#e65100"
    return "#b71c1c"


def _make_soil_chart_json(fields_data: list[dict], short_labels: dict[str, str]) -> tuple[str, str]:
    def _field_num(f: dict) -> int:
        label = short_labels.get(f["field_id"], "")
        m = re.search(r"(\d+)$", label)
        return int(m.group(1)) if m else 9999

    ordered_fields = sorted(fields_data, key=_field_num)
    labels = [short_labels.get(f["field_id"], f["field_id"]) for f in ordered_fields]
    soil_scores = [f.get("soil_condition_screening_score") or 0 for f in ordered_fields]
    colors = [_soil_category_color(s) for s in soil_scores]
    trace = {
        "type": "bar",
        "x": labels,
        "y": soil_scores,
        "marker": {"color": colors},
        "textposition": "none",
        "hovertemplate": "<b>%{x}</b><br>Soil Condition Screening Score: %{y:.1f}<br>Category: %{text}<extra></extra>",
        "text": [
            f"{f.get('crop', 'N/A')} · {f['field_id']} · {f.get('soil_screening_category', '')}"
            for f in ordered_fields
        ],
    }
    layout = {
        "title": {"text": "Soil Condition Screening Score by Field"},
        "yaxis": {"title": "Score (0-100)", "range": [0, 105]},
        "height": 350,
        "margin": {"t": 40, "b": 100, "l": 60, "r": 20},
        "shapes": [
            {"type": "line", "y0": 80, "y1": 80, "x0": -0.5, "x1": len(labels) - 0.5,
             "line": {"dash": "dash", "color": "#1b5e20", "width": 1.5}},
            {"type": "line", "y0": 65, "y1": 65, "x0": -0.5, "x1": len(labels) - 0.5,
             "line": {"dash": "dash", "color": "#2e7d32", "width": 1}},
            {"type": "line", "y0": 50, "y1": 50, "x0": -0.5, "x1": len(labels) - 0.5,
             "line": {"dash": "dash", "color": "#ff8f00", "width": 1}},
            {"type": "line", "y0": 35, "y1": 35, "x0": -0.5, "x1": len(labels) - 0.5,
             "line": {"dash": "dash", "color": "#e65100", "width": 1}},
        ],
        "annotations": [
            {"x": len(labels) - 0.5, "y": 82, "text": "Strong screening (80+)", "showarrow": False, "font": {"size": 10, "color": "#1b5e20"}},
            {"x": len(labels) - 0.5, "y": 72, "text": "Moderately strong (65+)", "showarrow": False, "font": {"size": 10, "color": "#2e7d32"}},
            {"x": len(labels) - 0.5, "y": 57, "text": "Moderate (50+)", "showarrow": False, "font": {"size": 10, "color": "#ff8f00"}},
            {"x": len(labels) - 0.5, "y": 42, "text": "Constrained (35+)", "showarrow": False, "font": {"size": 10, "color": "#e65100"}},
        ],
    }
    return json.dumps([trace], cls=_NumpyEncoder), json.dumps(layout, cls=_NumpyEncoder)


def _make_weather_radar_json(fields_data: list[dict]) -> tuple[str, str]:
    traces = []
    for i, f in enumerate(fields_data):
        weather = f.get("weather_suitability_score")
        if weather is not None:
            traces.append({
                "type": "bar",
                "name": f["field_id"],
                "x": [f["field_id"]],
                "y": [weather],
                "marker": {"color": _color_for_field(i)},
                "hovertemplate": f["field_id"] + "<br>Weather Suitability: %{y:.1f}<extra></extra>",
            })
    layout = {
        "title": {"text": "Weather Suitability Score"},
        "yaxis": {"title": "Score (0-100)", "range": [0, 105]},
        "height": 350,
        "margin": {"t": 40, "b": 100, "l": 60, "r": 20},
    }
    return json.dumps(traces, cls=_NumpyEncoder), json.dumps(layout, cls=_NumpyEncoder)


def _make_gdd_precip_chart_json(fields_data: list[dict], weather_timeseries: pd.DataFrame | None) -> tuple[str, str]:
    traces = []
    if weather_timeseries is not None and not weather_timeseries.empty:
        wf = weather_timeseries.copy()
        wf["date"] = pd.to_datetime(wf["date"])
        wf = wf.sort_values("date")
        for i, fid in enumerate(set(wf["field_id"])):
            fd = wf[wf["field_id"] == fid]
            if fd.empty:
                continue
            fd = fd.sort_values("date")
            gdd_base = 10
            fd["tavg"] = (fd["T2M_MAX"] + fd["T2M_MIN"]) / 2
            fd["gdd"] = (fd["tavg"] - gdd_base).clip(lower=0)
            fd["cum_gdd"] = fd["gdd"].cumsum()
            traces.append({
                "type": "scatter",
                "mode": "lines",
                "name": f"{fid} GDD",
                "x": fd["date"].dt.strftime("%Y-%m-%d").tolist(),
                "y": fd["cum_gdd"].round(1).tolist(),
                "yaxis": "y",
                "line": {"color": _color_for_field(i)},
                "hovertemplate": f"{fid}<br>Date: %{{x}}<br>Cumulative GDD: %{{y:.0f}}<extra></extra>",
            })
            fd["month"] = fd["date"].dt.to_period("M").dt.to_timestamp()
            monthly_precip = fd.groupby("month")["PRECTOTCORR"].sum().reset_index()
            monthly_precip = monthly_precip.sort_values("month")
            traces.append({
                "type": "bar",
                "name": f"{fid} Precip",
                "x": monthly_precip["month"].dt.strftime("%Y-%m-%d").tolist(),
                "y": monthly_precip["PRECTOTCORR"].round(1).tolist(),
                "yaxis": "y2",
                "marker": {"color": _color_for_field(i), "opacity": 0.3},
                "hovertemplate": f"{fid}<br>%{{x}}<br>Rainfall: %{{y:.0f}} mm<extra></extra>",
                "showlegend": False,
            })
    layout = {
        "title": {"text": "Growing Degree Days and Monthly Precipitation"},
        "yaxis": {"title": "Cumulative GDD"},
        "yaxis2": {"title": "Precipitation (mm)", "overlaying": "y", "side": "right"},
        "height": 400,
        "margin": {"t": 40, "b": 80, "l": 60, "r": 60},
        "legend": {"orientation": "h", "y": -0.3},
    }
    return json.dumps(traces, cls=_NumpyEncoder), json.dumps(layout, cls=_NumpyEncoder)


def _make_stress_chart_json(fields_data: list[dict]) -> tuple[str, str]:
    labels = [f["field_id"] for f in fields_data]
    stress = [f.get("crop_stress") or 0 for f in fields_data]
    colors = ["#2ca02c" if s <= 30 else "#ff7f0e" if s <= 60 else "#d62728" for s in stress]
    trace = {
        "type": "bar",
        "x": labels,
        "y": stress,
        "marker": {"color": colors},
        "hovertemplate": "<b>%{x}</b><br>Apparent Stress: %{y:.1f}/100<br>%{text}<extra></extra>",
        "text": ["Low" if s <= 30 else "Moderate" if s <= 60 else "High" for s in stress],
    }
    layout = {
        "title": {"text": "Apparent Crop Stress Indicator"},
        "yaxis": {"title": "Apparent Stress (0-100)", "range": [0, 105]},
        "height": 350,
        "margin": {"t": 40, "b": 100, "l": 60, "r": 20},
        "shapes": [
            {"type": "line", "y0": 30, "y1": 30, "x0": -0.5, "x1": len(labels) - 0.5,
             "line": {"dash": "dash", "color": "gray", "width": 1}},
            {"type": "line", "y0": 60, "y1": 60, "x0": -0.5, "x1": len(labels) - 0.5,
             "line": {"dash": "dash", "color": "gray", "width": 1}},
        ],
        "annotations": [
            {"x": len(labels) - 0.5, "y": 25, "text": "Low", "showarrow": False, "font": {"size": 10}},
            {"x": len(labels) - 0.5, "y": 45, "text": "Moderate", "showarrow": False, "font": {"size": 10}},
            {"x": len(labels) - 0.5, "y": 80, "text": "High", "showarrow": False, "font": {"size": 10}},
        ],
    }
    return json.dumps([trace], cls=_NumpyEncoder), json.dumps(layout, cls=_NumpyEncoder)


class _NumpyEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        return super().default(obj)


def _make_map_json(fields_data: list[dict], short_labels: dict[str, str]) -> tuple[str, str]:
    map_traces = []
    for i, f in enumerate(fields_data):
        for poly in f.get("geo_polygons", []):
            lons = [p[0] for p in poly] + [poly[0][0]]
            lats = [p[1] for p in poly] + [poly[0][1]]
            risk = f.get("risk_category", "")
            color = RISK_COLORS.get(risk, "#7f7f7f")
            short = _short_label(short_labels, f["field_id"], f.get("crop", ""))
            map_traces.append({
                "type": "scattermapbox",
                "mode": "lines",
                "fill": "toself",
                "name": f["field_id"],
                "lon": lons,
                "lat": lats,
                "line": {"color": color, "width": 2},
                "fillcolor": color + "60",
                "legendgroup": f["field_id"],
                "showlegend": i == 0,
                "hovertemplate": (
                    f"<b>{short}</b><br>"
                    f"FIS: {f.get('fis', 'N/A'):.0f}/100<br>"
                    f"Crop: {f.get('crop', 'N/A')}<br>"
                    f"Acres: {f.get('area_acres', 0):.0f}<br>"
                    f"NDVI Condition: {f.get('ndvi_condition_score', 'N/A')}/100<br>"
                    f"Soil Score: {f.get('soil_condition_screening_score', 'N/A')}/100<br>"
                    f"Risk: {risk}<extra></extra>"
                ),
            })
    all_lons = [p[0] for f in fields_data for poly in f.get("geo_polygons", []) for p in poly]
    all_lats = [p[1] for f in fields_data for poly in f.get("geo_polygons", []) for p in poly]
    if all_lons:
        center_lon = (min(all_lons) + max(all_lons)) / 2.0
        center_lat = (min(all_lats) + max(all_lats)) / 2.0
        first_field = fields_data[0].get("geo_polygons", [[]])[0]
        if first_field and len(first_field) > 2:
            flons = [p[0] for p in first_field]
            flats = [p[1] for p in first_field]
            pad_lon = (max(flons) - min(flons)) * 0.2
            pad_lat = (max(flats) - min(flats)) * 0.2
            field_span = max((max(flons) - min(flons)) + 2 * pad_lon,
                             (max(flats) - min(flats)) + 2 * pad_lat)
        else:
            field_span = max(max(all_lons) - min(all_lons), max(all_lats) - min(all_lats))
        zoom = max(13, min(17, 15 - math.log2(max(field_span, 0.001) * 10)))
    else:
        center_lon, center_lat, zoom = -98.5, 40.9, 10

    layout = {
        "title": {"text": "Field Boundaries — Risk Category (color)"},
        "showlegend": False,
        "height": 500,
        "margin": {"t": 40, "b": 20, "l": 20, "r": 20},
        "dragmode": "zoom",
        "mapbox": {
            "style": "open-street-map",
            "center": {"lat": center_lat, "lon": center_lon},
            "zoom": zoom,
        },
    }
    return json.dumps(map_traces, cls=_NumpyEncoder), json.dumps(layout, cls=_NumpyEncoder)


def _make_kpi_html(fields_data: list[dict]) -> str:
    total_acres = sum(f.get("area_acres") or 0 for f in fields_data)
    rc_fields = [f for f in fields_data if f.get("row_crop_fis_score") is not None]
    ref_fields = [f for f in fields_data if f.get("row_crop_fis_score") is None]
    n_rc = len(rc_fields)
    n_ref = len(ref_fields)
    rc_acres = sum(f.get("area_acres") or 0 for f in rc_fields)

    rc_fis_vals = [f.get("row_crop_fis_score") for f in rc_fields if f.get("row_crop_fis_score") is not None]
    rc_mean_fis = sum(rc_fis_vals) / len(rc_fis_vals) if rc_fis_vals else 0

    ndvi_vals = [f.get("ndvi_condition_score") for f in rc_fields if f.get("ndvi_condition_score") is not None]
    mean_ndvi = sum(ndvi_vals) / len(ndvi_vals) if ndvi_vals else 0

    fis_vals = [f.get("row_crop_fis_score") for f in rc_fields if f.get("row_crop_fis_score") is not None]
    n_attention = sum(1 for v in fis_vals if v < 50) if fis_vals else 0

    attention_cls = "attention ok" if n_attention == 0 else "attention"

    precip_vals = [f.get("total_precipitation_mm") for f in fields_data if f.get("total_precipitation_mm") is not None]
    mean_precip = sum(precip_vals) / len(precip_vals) if precip_vals else 0
    gdd_vals = [f.get("cumulative_gdd") for f in fields_data if f.get("cumulative_gdd") is not None]
    mean_gdd = sum(gdd_vals) / len(gdd_vals) if gdd_vals else 0
    ref_fis_vals = [f.get("fis") for f in ref_fields if f.get("fis") is not None]
    ref_mean_fis = sum(ref_fis_vals) / len(ref_fis_vals) if ref_fis_vals else 0

    cards = f"""
    <div class="kpi-row">
        <div class="kpi-card"><div class="value">{n_rc}</div><div class="label">Row-crop fields</div></div>
        <div class="kpi-card"><div class="value">{rc_acres:,.0f}</div><div class="label">Row-crop acres</div></div>
        <div class="kpi-card"><div class="value">{rc_mean_fis:.0f}/100</div><div class="label">Mean Row-Crop FIS</div></div>
        <div class="kpi-card {attention_cls}"><div class="value">{n_attention}</div><div class="label">Fields needing attention</div></div>
        <div class="kpi-card"><div class="value">{mean_ndvi:.0f}/100</div><div class="label">Mean Crop Condition</div></div>
    </div>
    <div class="metadata-line">
        Total fields: {len(fields_data)} · Row-crop fields: {n_rc} · Others: {n_ref} ·
        Mean rainfall: {mean_precip:.0f} mm · Mean GDD: {mean_gdd:.0f}
        {f'· Reference FIS: {ref_mean_fis:.0f}/100' if ref_mean_fis > 0 else ''}
    </div>"""
    return cards


def _make_priority_summary_html(fields_data: list[dict], short_labels: dict[str, str]) -> str:
    rc_fields = [f for f in fields_data if f.get("row_crop_fis_score") is not None]
    rc_sorted = sorted(rc_fields, key=lambda f: f.get("row_crop_fis_score") or 0, reverse=True)

    top_field = rc_sorted[0] if rc_sorted else None
    bottom_field = rc_sorted[-1] if rc_sorted else None

    rc_fis_vals = [f.get("row_crop_fis_score") for f in rc_fields if f.get("row_crop_fis_score") is not None]
    n_attention = sum(1 for v in rc_fis_vals if v < 50) if rc_fis_vals else 0

    mean_fis = sum(rc_fis_vals) / len(rc_fis_vals) if rc_fis_vals else 0
    if mean_fis >= 65:
        overall = f"Overall row-crop condition is favourable"
    elif mean_fis >= 50:
        overall = f"Overall row-crop condition is moderate"
    else:
        overall = f"Overall row-crop condition needs attention"

    drivers = []
    if bottom_field:
        stressors = []
        if bottom_field.get("crop_stress") is not None and bottom_field.get("crop_stress") > 50:
            stressors.append("elevated apparent stress")
        if bottom_field.get("ndvi_condition_score") is not None and bottom_field.get("ndvi_condition_score") < 50:
            stressors.append("low crop-condition scores")
        soil = bottom_field.get("soil_condition_screening_score")
        if soil is not None and soil < 50:
            low_comp = []
            om_s = bottom_field.get("om_score")
            ph_s = bottom_field.get("ph_suitability_score")
            awc_s = bottom_field.get("awc_score")
            dr_s = bottom_field.get("drainage_score")
            cec_s = bottom_field.get("cec_score")
            if om_s is not None and om_s < 40: low_comp.append("low organic matter")
            if ph_s is not None and ph_s < 40: low_comp.append("pH limitations")
            if awc_s is not None and awc_s < 40: low_comp.append("low AWC")
            if dr_s is not None and dr_s < 40: low_comp.append("drainage limitations")
            if cec_s is not None and cec_s < 40: low_comp.append("low CEC")
            if low_comp:
                stressors.append(", ".join(low_comp))
        if stressors:
            drivers.append(f"{_short_label(short_labels, bottom_field['field_id'], bottom_field.get('crop', ''))} shows {'; '.join(stressors)}.")

    if n_attention == 1:
        attn_text = "1 field needs attention"
        attn_label = "1 field"
    elif n_attention > 1:
        attn_text = f"{n_attention} fields need attention"
        attn_label = f"{n_attention} fields"
    else:
        attn_text = "All row-crop fields are above threshold"
        attn_label = "0 fields"

    top_label = _short_label(short_labels, top_field["field_id"], top_field.get("crop", "")) if top_field else "N/A"
    bottom_label = _short_label(short_labels, bottom_field["field_id"], bottom_field.get("crop", "")) if bottom_field else "N/A"
    bottom_fis = f"{bottom_field['row_crop_fis_score']:.0f}" if bottom_field and bottom_field.get("row_crop_fis_score") else "N/A"

    html = f"""
    <div class="panel" id="priorityPanel">
        <div class="row">
            <div class="stat"><span class="val">Best:</span> {top_label}</div>
            <div class="stat"><span class="val">Lowest:</span> {bottom_label} ({bottom_fis} FIS)</div>
            <div class="stat"><span class="val">Needs attention:</span> {attn_label}</div>
        </div>
        <div style="margin-top:8px;font-size:var(--font-sm);color:var(--muted);">
            {overall}, with {attn_text}.{' ' + ' '.join(drivers) if drivers else ''}
        </div>
    </div>"""
    return html


def _make_selected_field_panel_html(f: dict, short_labels: dict[str, str]) -> str:
    label = _short_label(short_labels, f["field_id"], f.get("crop", ""))
    fis = f.get("row_crop_fis_score") or f.get("fis")
    fis_str = f"{fis:.0f}/100" if fis is not None else "N/A"
    ndvi = f.get("ndvi_condition_score")
    ndvi_str = f"{ndvi:.0f}/100" if ndvi is not None else "N/A"
    stress = f.get("crop_stress")
    stress_str = f"{stress:.0f}/100" if stress is not None else "N/A"
    soil = f.get("soil_condition_screening_score")
    soil_str = f"{soil:.0f}/100" if soil is not None else "N/A"
    acres = f.get("area_acres", 0)
    conf = f.get("fis_confidence", "")
    soil_cat = f.get("soil_screening_category", "")

    # Find main limitation (lowest component)
    comps = {
        "OM": f.get("om_score"), "pH": f.get("ph_suitability_score"),
        "AWC": f.get("awc_score"), "Drainage": f.get("drainage_score"),
        "CEC": f.get("cec_score"),
    }
    valid_comps = {k: v for k, v in comps.items() if v is not None}
    limitation = ""
    if valid_comps:
        min_k = min(valid_comps, key=valid_comps.get)
        min_v = valid_comps[min_k]
        if min_v < 40:
            limitation = f"Main limitation: {min_k} ({min_v:.0f}/100)"
        else:
            limitation = "No significant limitations"

    # Recommended action
    action = "Continue routine monitoring."
    if fis is not None and fis < 50:
        action = "Priority field — consider field scouting and targeted soil sampling."
    elif fis is not None and fis < 65:
        action = "Monitor closely — check crop condition and soil moisture."
    elif stress is not None and stress > 60:
        action = "Elevated apparent stress — investigate possible causes."

    html = f"""
    <div class="panel" id="selectedFieldPanel" style="display:none;">
        <div class="row">
            <div class="stat"><span class="val">{label}</span> · {acres:.0f} ac</div>
            <div class="stat">FIS: {fis_str}</div>
            <div class="stat">Crop condition: {ndvi_str}</div>
            <div class="stat">Stress: {stress_str}</div>
            <div class="stat">Soil: {soil_str}</div>
            <div class="stat">Confidence: {conf}</div>
        </div>
        <div style="margin-top:8px;font-size:var(--font-sm);color:var(--muted);">
            {limitation}{' · ' if limitation else ''}{soil_cat}{' · ' if soil_cat else ''}{action}
        </div>
    </div>"""
    return html


def _make_field_table_html(fields_data: list[dict], short_labels: dict[str, str]) -> str:
    rows_html = ""
    for f in sorted(fields_data, key=lambda x: x.get("fis") or 0, reverse=True):
        fid = f["field_id"]
        short = _short_label(short_labels, fid)
        fis = f.get("fis")
        fis_str = f"{fis:.0f}" if fis is not None else "N/A"
        crop = f.get("crop", "N/A")
        acres = f.get("area_acres", 0)
        stress = f.get("crop_stress")
        stress_str = f"{stress:.0f}" if stress is not None else "N/A"
        stress_color = "#d62728" if stress is not None and stress >= 60 else "#333"
        soil = f.get("soil_condition_screening_score")
        soil_str = f"{soil:.0f}" if soil is not None else "N/A"
        priority = f.get("priority_category", "")
        highlight = "highlight" if fis is not None and fis < 50 else ""

        rc_fis = f.get("row_crop_fis_score")
        rc_fis_str = f"{rc_fis:.0f}" if rc_fis is not None else "—"
        rc_rank = f.get("row_crop_fis_rank")
        rc_rank_str = ""
        if rc_rank is not None and not (isinstance(rc_rank, float) and math.isnan(rc_rank)):
            rc_rank_str = f"#{int(rc_rank)}"
        ndvi_cond = f.get("ndvi_condition_score")
        ndvi_cond_str = f"{ndvi_cond:.0f}" if ndvi_cond is not None else "N/A"
        conf = f.get("fis_confidence", "")
        risk = f.get("risk_category", "")

        detail_html = f"""
        <tr class="field-detail" style="display:none;background:#f9fafb;" data-field-id="{fid}" data-crop="{crop}" data-risk="{risk}">
            <td colspan="5" style="padding:10px 14px;font-size:var(--font-sm);line-height:1.7;">
                <div class="detail-grid">
                    <span><strong>Full ID:</strong> {fid}</span>
                    <span><strong>Crop:</strong> {crop}</span>
                    <span><strong>Acres:</strong> {acres:.0f}</span>
                    <span><strong>NDVI Condition:</strong> {ndvi_cond_str}/100</span>
                    <span><strong>Row-crop FIS:</strong> {rc_fis_str} {rc_rank_str}</span>
                    <span><strong>Risk:</strong> {risk}</span>
                    <span><strong>Confidence:</strong> {conf}</span>
                    <span><strong>Soil screening:</strong> {soil_str}/100</span>
                    <span><strong>Stress:</strong> {stress_str}/100</span>
                    <span><strong>Priority:</strong> {priority}</span>
                </div>
            </td>
        </tr>"""

        rows_html += f"""
        <tr class="field-primary {highlight}" onclick="toggleFieldDetail(this)" style="cursor:pointer;" data-field-id="{fid}" data-crop="{crop}" data-risk="{risk}">
            <td style="min-width:130px;">{short}<br><span style="font-size:var(--font-sm);color:var(--muted);">{crop} · {acres:.0f} ac</span></td>
            <td style="text-align:center;"><b>{fis_str}</b></td>
            <td style="text-align:center;color:{stress_color};">{stress_str}</td>
            <td style="text-align:center;">{soil_str}</td>
            <td style="text-align:center;"><span class="tag tag-{priority.lower()}">{priority}</span></td>
        </tr>{detail_html}"""
    return f"""
    <div style="overflow-x:auto;">
    <p style="font-size:var(--font-sm);color:var(--muted);margin-bottom:8px;">Click a row to expand details.</p>
    <table>
        <thead>
            <tr>
                <th style="min-width:130px;">Field</th>
                <th style="text-align:center;">FIS</th>
                <th style="text-align:center;">Stress</th>
                <th style="text-align:center;">Soil</th>
                <th style="text-align:center;">Priority</th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
    <script>
    function toggleFieldDetail(row) {{
        var next = row.nextElementSibling;
        if (next && next.classList.contains('field-detail')) {{
            var isHidden = next.style.display === 'none' || next.style.display === '';
            next.style.display = isHidden ? 'table-row' : 'none';
            if (typeof dashboardState !== 'undefined') {{
                dashboardState.expandedFieldId = isHidden ? row.getAttribute('data-field-id') : null;
            }}
        }}
    }}
    </script>
    </div>"""


def _depth_display(val) -> str:
    if val is not None:
        return f"0-{int(val)} cm"
    return "Source depth unavailable in current processed dataset"


def _unavail(val: Any, fmt: str | None = None) -> str:
    """Return formatted value if present, else explicit unavailability text."""
    if val is None or (isinstance(val, float) and math.isnan(val)) or (isinstance(val, str) and val.strip() == ""):
        return "Not available in current processed data"
    if fmt:
        return fmt.format(val)
    return str(val)


def _make_soil_detail_html_simple(f: dict) -> str:
    soil_score = f.get("soil_condition_screening_score")
    soil_score_str = f"{soil_score:.0f}" if soil_score is not None else "N/A"
    category = f.get("soil_screening_category", "")
    conf = f.get("soil_score_confidence", "N/A")
    cons = f.get("conservation_priority")
    cons_str = f"{cons:.0f}" if cons is not None else "N/A"
    missing = f.get("soil_components_missing", "")
    missing_note = f"Missing components: {missing}" if missing and missing != "none" else "All soil components available"

    om_score = f.get("om_score"); om_score_str = f"{om_score:.0f}" if om_score is not None else "N/A"
    ph_score = f.get("ph_suitability_score"); ph_score_str = f"{ph_score:.0f}" if ph_score is not None else "N/A"
    awc_score = f.get("awc_score"); awc_score_str = f"{awc_score:.0f}" if awc_score is not None else "N/A"
    dr_score = f.get("drainage_score"); dr_score_str = f"{dr_score:.0f}" if dr_score is not None else "N/A"
    cec_score = f.get("cec_score"); cec_score_str = f"{cec_score:.0f}" if cec_score is not None else "N/A"
    om_raw = f.get("organic_matter_pct"); om_raw_str = f"{om_raw:.2f}%" if om_raw is not None else "N/A"
    ph_raw = f.get("soil_ph"); ph_raw_str = f"{ph_raw:.2f}" if ph_raw is not None else "N/A"
    awc_raw = f.get("available_water_capacity_in"); awc_raw_str = f"{awc_raw:.2f} in" if awc_raw is not None else "N/A"
    drainage = f.get("drainage_class", "N/A")
    cec_raw = f.get("cec_meq100g"); cec_raw_str = f"{cec_raw:.1f}" if cec_raw is not None else "N/A"

    erosion = f.get("erosion_risk", "N/A")
    dom_mu = f.get("dominant_mapunit_name", "N/A")
    method = f.get("soil_aggregation_method", "N/A")

    return f"""
    <tr class="soil-detail" style="display:none;background:#f8f9fa;" data-soil-field-id="{f.get('field_id', '')}">
        <td colspan="5" style="padding:12px 16px;font-size:var(--font-sm);line-height:1.7;">
            <div class="accordion">
                <details>
                    <summary>Condition Components (scores 0–100)</summary>
                    <div class="content">
                        <div class="detail-grid">
                            <span>OM: {om_score_str} (raw: {om_raw_str})</span>
                            <span>pH: {ph_score_str} (raw: {ph_raw_str})</span>
                            <span>AWC: {awc_score_str} (raw: {awc_raw_str})</span>
                            <span>Drainage: {dr_score_str} ({drainage})</span>
                            <span>CEC: {cec_score_str} (raw: {cec_raw_str})</span>
                        </div>
                    </div>
                </details>
                <details>
                    <summary>Data Confidence</summary>
                    <div class="content">
                        <div>Confidence: {conf}</div>
                        <div>{missing_note}</div>
                        <div>Aggregation: {method}</div>
                    </div>
                </details>
                <details>
                    <summary>Conservation Evidence</summary>
                    <div class="content">
                        <div>Conservation priority score: {cons_str}</div>
                        <div>Erosion risk: {erosion}</div>
                        <div>Map unit: {dom_mu}</div>
                    </div>
                </details>
            </div>
        </td>
    </tr>"""


def _make_soil_limits_summary(fields_data: list[dict]) -> str:
    issues = {"drainage": 0, "low OM": 0, "pH limitation": 0, "low AWC": 0, "low CEC": 0}
    total = len(fields_data)
    for f in fields_data:
        dr = f.get("drainage_score")
        if dr is not None and dr < 40:
            issues["drainage"] += 1
        om = f.get("om_score")
        if om is not None and om < 40:
            issues["low OM"] += 1
        ph = f.get("ph_suitability_score")
        if ph is not None and ph < 40:
            issues["pH limitation"] += 1
        awc = f.get("awc_score")
        if awc is not None and awc < 40:
            issues["low AWC"] += 1
        cec = f.get("cec_score")
        if cec is not None and cec < 40:
            issues["low CEC"] += 1
    active = {k: v for k, v in issues.items() if v > 0}
    if not active:
        return '<p id="soilSummary" style="font-size:var(--font-sm);color:var(--muted);margin-bottom:8px;">No significant soil limitations identified among the current fields.</p>'
    sorted_issues = sorted(active.items(), key=lambda x: -x[1])
    label_map = {
        "drainage": "poor drainage",
        "low OM": "low organic matter",
        "pH limitation": "pH limitation",
        "low AWC": "low available water capacity",
        "low CEC": "low CEC",
    }
    parts = [f"{label_map[name]} in {cnt} of {total} fields" for name, cnt in sorted_issues[:2] if cnt > 0]
    if not parts:
        return ""
    summary = parts[0] if len(parts) == 1 else f"{parts[0]} and {parts[1]}"
    return f'<p id="soilSummary" style="font-size:var(--font-sm);color:var(--muted);margin-bottom:8px;">Common screening limitations: {summary}.</p>'


def _make_soil_table_html(fields_data: list[dict], short_labels: dict[str, str]) -> str:
    rows_html = ""
    for f in sorted(fields_data, key=lambda x: x.get("soil_condition_screening_score") or 0, reverse=True):
        soil_score = f.get("soil_condition_screening_score")
        soil_score_str = f"{soil_score:.0f}" if soil_score is not None else "N/A"
        category = f.get("soil_screening_category", "")
        om = f.get("organic_matter_pct")
        om_str = f"{om:.2f}%" if om is not None else "N/A"
        ph = f.get("soil_ph")
        ph_str = f"{ph:.2f}" if ph is not None else "N/A"
        awc = f.get("available_water_capacity_in")
        awc_str = f"{awc:.2f} in" if awc is not None else "N/A"

        detail = _make_soil_detail_html_simple(f)
        short = _short_label(short_labels, f['field_id'])
        rows_html += f"""
        <tr class="soil-primary" onclick="toggleSoilDetail(this)" style="cursor:pointer;" data-soil-field-id="{f['field_id']}">
            <td>{short}<br><span style="font-size:var(--font-sm);color:var(--muted);">{f.get('crop', 'N/A')}</span></td>
            <td style="text-align:center;"><b>{soil_score_str}</b><br><span style="font-size:var(--font-sm);color:var(--muted);">{category}</span></td>
            <td style="text-align:center;">{om_str}</td>
            <td style="text-align:center;">{ph_str}</td>
            <td style="text-align:center;">{awc_str}</td>
        </tr>{detail}"""
    return f"""
    <div style="overflow-x:auto;">
    <p style="font-size:var(--font-sm);color:var(--muted);margin-bottom:8px;">Click a row to expand condition components, data confidence, and conservation evidence.</p>
    <table>
        <thead>
            <tr>
                <th>Field</th>
                <th style="text-align:center;">Score</th>
                <th style="text-align:center;">OM</th>
                <th style="text-align:center;">pH</th>
                <th style="text-align:center;">AWC</th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
    <script>
    function toggleSoilDetail(row) {{
        var next = row.nextElementSibling;
        if (next && next.classList.contains('soil-detail')) {{
            var isHidden = next.style.display === 'none' || next.style.display === '';
            next.style.display = isHidden ? 'table-row' : 'none';
            if (typeof dashboardState !== 'undefined') {{
                dashboardState.expandedSoilFieldId = isHidden ? row.getAttribute('data-soil-field-id') : null;
            }}
        }}
    }}
    </script>
    </div>"""


def render_dashboard(d: DashboardData, output_path: str | Path, title: str = "Row Crop Intelligence Dashboard"):
    fields_data = _build_field_data(d.field_summary, d.field_boundaries)
    meta = d.metadata
    grower_id = meta.get("grower_id", "") if meta else ""
    year = meta.get("year", "") if meta else ""
    field_count = meta.get("field_count", len(fields_data)) if meta else len(fields_data)
    crops = [f.get("crop", "") for f in fields_data if f.get("crop")]
    primary_crop = max(set(crops), key=crops.count) if crops else ""

    short_labels = _make_short_labels(fields_data)

    fis_traces, fis_layout, ref_traces, ref_layout = _make_ranking_chart_json(fields_data, short_labels)
    ndvi_cond_trace, ndvi_stress_trace, ndvi_layout = _make_ndvi_chart_json(fields_data, short_labels)
    soil_traces, soil_layout = _make_soil_chart_json(fields_data, short_labels)
    weather_traces, weather_layout = _make_weather_radar_json(fields_data)
    gdd_precip_traces, gdd_precip_layout = _make_gdd_precip_chart_json(fields_data, d.weather_timeseries)
    map_traces, map_layout = _make_map_json(fields_data, short_labels)

    kpi_html = _make_kpi_html(fields_data)
    priority_html = _make_priority_summary_html(fields_data, short_labels)
    table_html = _make_field_table_html(fields_data, short_labels)
    soil_table_html = _make_soil_table_html(fields_data, short_labels)
    soil_limits_html = _make_soil_limits_summary(fields_data)

    ref_fields = [f for f in fields_data if f.get("row_crop_fis_score") is None]
    ref_crops = set(f.get("crop", "") for f in ref_fields if f.get("crop"))
    ref_summary = f'{len(ref_fields)} reference area(s): {", ".join(sorted(ref_crops))}' if ref_crops else ""
    soil_limits_html = _make_soil_limits_summary(fields_data)

    plotly_cdn = "https://cdn.plot.ly/plotly-2.35.2.min.js"

    field_dropdown_options = ''.join(
        f'<option value="{f["field_id"]}">{_short_label(short_labels, f["field_id"], f.get("crop", ""))}</option>'
        for f in fields_data
    )

    data_warnings = []
    n_fields = len(fields_data)
    scene_field_ids = set(d.field_summary.loc[d.field_summary["valid_ndvi_observations"].notna() & (d.field_summary["valid_ndvi_observations"] > 0), "field_id"].tolist())
    n_with_scenes = len(scene_field_ids)
    if n_with_scenes < n_fields:
        data_warnings.append(
            f"Only {n_with_scenes} of {n_fields} fields have satellite scene data. "
            f"NDVI for the other {n_fields - n_with_scenes} fields is derived from interpolated or card-summary values."
        )
    if d.weather_timeseries is not None and not d.weather_timeseries.empty:
        unique_lat_lon = d.weather_timeseries[["lat", "lon"]].drop_duplicates().shape[0]
        data_warnings.append(
            f"Weather data sourced from NASA POWER at ~0.5° grid resolution "
            f"({unique_lat_lon} unique grid point{'s' if unique_lat_lon > 1 else ''}). "
            f"Values may differ from on-farm conditions."
        )
    drainage_classes = set(f.get("drainage_class", "") for f in fields_data if f.get("drainage_class"))
    if len(drainage_classes) <= 1 and drainage_classes:
        dc = next(iter(drainage_classes))
        data_warnings.append(
            f"All fields share the same drainage class ('{dc}'). "
            f"Soil variability may be underrepresented."
        )

    warnings_html = ""
    if data_warnings:
        warnings_html = '<div style="margin:16px 0;padding:12px 16px;background:#fff8e1;border:1px solid #ffe082;border-radius:8px;font-size:13px;line-height:1.6;">'
        warnings_html += '<strong style="color:#e65100;">Data quality notes:</strong>'
        for w in data_warnings:
            warnings_html += f'<div style="margin-top:4px;">• {w}</div>'
        warnings_html += '</div>'

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="{plotly_cdn}"></script>
        <style>
        :root {{
            --accent: #2f6b3f; --accent-strong: #214d2d; --accent-light: #eef5ef;
            --bg: #f6f4ef; --card-bg: #fffdfa; --border: #ddd6c8;
            --text: #1f1d18; --muted: #6e6658;
            --green: #2f6b3f; --amber: #b9872d; --red: #a64d3c;
            --soil: #7a6443; --soil-light: #f4efe6;
            --water: #587a8b; --water-light: #eef4f7;
            --harvest-light: #fbf4e6;
            --font-title: 28px; --font-h2: 20px; --font-h3: 16px;
            --font-body: 14px; --font-sm: 12px;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); line-height: 1.6; }}
        .container {{ max-width: 1360px; margin: 0 auto; padding: 24px; }}
        .header {{ background: linear-gradient(180deg, #fffdfa 0%, #f7f2e8 100%); border-bottom: 3px solid var(--accent); padding: 20px 24px; margin-bottom: 24px; box-shadow: 0 4px 14px rgba(82, 67, 40, 0.06); }}
        .header h1 {{ font-size: var(--font-title); font-weight: 700; color: var(--text); margin: 0; }}
        .section {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 24px; margin-bottom: 28px; box-shadow: 0 8px 20px rgba(85, 73, 49, 0.05); }}
        .section h2 {{ font-size: var(--font-h2); font-weight: 600; color: var(--text); margin-bottom: 16px; padding-bottom: 10px; border-bottom: 2px solid var(--accent-light); }}
        .section h3 {{ font-size: var(--font-h3); font-weight: 600; color: var(--text); margin-bottom: 12px; }}
        .section-overview {{ background: linear-gradient(180deg, #fffdfa 0%, var(--accent-light) 100%); border-top: 4px solid var(--accent); }}
        .section-health {{ background: linear-gradient(180deg, #fffdfa 0%, var(--harvest-light) 100%); border-top: 4px solid var(--amber); }}
        .section-soil {{ background: linear-gradient(180deg, #fffdfa 0%, var(--soil-light) 100%); border-top: 4px solid var(--soil); }}
        .section-secondary {{ background: linear-gradient(180deg, #fffdfa 0%, #f7f4ee 100%); }}
        .section-weather details > summary {{ background: var(--water-light); border-radius: 6px; padding: 10px 12px; }}
        .section-provenance details > summary {{ background: #f6f1e9; border-radius: 6px; padding: 10px 12px; }}
        .section-method details > summary {{ background: var(--accent-light); border-radius: 6px; padding: 10px 12px; }}
        .chart-container {{ width: 100%; margin: 12px 0; }}
        .kpi-row {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 12px; margin-bottom: 16px; }}
        .kpi-card {{ background: rgba(255,255,255,0.88); border: 1px solid var(--border); border-radius: 8px; padding: 16px; text-align: center; box-shadow: inset 0 1px 0 rgba(255,255,255,0.7); }}
        .kpi-row .kpi-card:first-child {{ background: var(--accent-light); border-color: #cfdccf; }}
        .kpi-card .value {{ font-size: 26px; font-weight: 700; color: var(--text); }}
        .kpi-card .label {{ font-size: var(--font-sm); color: var(--muted); margin-top: 2px; }}
        .kpi-card.attention {{ border-left: 3px solid var(--amber); }}
        .kpi-card.attention.ok {{ border-left-color: var(--green); }}
        .kpi-card.attention .value {{ color: var(--amber); }}
        .kpi-card.attention.ok .value {{ color: var(--green); }}
        .metadata-line {{ font-size: var(--font-sm); color: var(--muted); margin: -8px 0 12px 0; text-align: center; }}
        .filters {{ display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-bottom: 16px; padding: 12px; background: linear-gradient(180deg, #fbf8f1 0%, #f4efe6 100%); border: 1px solid #d7ccb8; border-radius: 8px; }}
        .filters label {{ font-size: var(--font-sm); font-weight: 600; color: var(--muted); }}
        .filters select {{ padding: 6px 10px; border: 1px solid #cdbfa8; border-radius: 4px; font-size: var(--font-sm); background: #fffdfa; color: var(--text); }}
        .filters .btn-reset {{ padding: 6px 14px; border: 1px solid #cdbfa8; border-radius: 4px; font-size: var(--font-sm); background: #fffdfa; cursor: pointer; color: var(--muted); }}
        .filters .btn-reset:hover {{ background: #f4efe6; }}
        .field-multiselect {{ position: relative; min-width: 220px; }}
        .field-multiselect button {{ width: 100%; padding: 6px 10px; border: 1px solid #c4d7c8; border-radius: 4px; font-size: var(--font-sm); background: var(--accent-light); cursor: pointer; color: var(--text); text-align: left; }}
        .field-multiselect button:hover {{ background: #e4f0e6; border-color: #aac2b0; }}
        .field-menu {{ position: absolute; top: calc(100% + 4px); left: 0; width: 320px; max-height: 260px; overflow: auto; background: #fffdfa; border: 1px solid #d7ccb8; border-radius: 6px; box-shadow: 0 10px 24px rgba(60,45,20,0.12); padding: 8px; z-index: 20; display: none; }}
        .field-menu.open {{ display: block; }}
        .field-menu-actions {{ display: flex; justify-content: space-between; gap: 8px; margin-bottom: 8px; }}
        .field-menu-actions button {{ width: auto; padding: 4px 8px; font-size: var(--font-sm); background: var(--soil-light); border: 1px solid #d7ccb8; border-radius: 4px; }}
        .field-option {{ display: flex; align-items: center; gap: 8px; padding: 6px 4px; font-size: var(--font-sm); border-radius: 4px; }}
        .field-option:hover {{ background: #f5f0e7; }}
        .field-option input {{ margin: 0; }}
        .panel {{ background: rgba(255,255,255,0.82); border: 1px solid var(--border); border-radius: 8px; padding: 16px; margin-bottom: 16px; font-size: var(--font-body); line-height: 1.7; }}
        .section-overview .panel {{ background: rgba(238,245,239,0.7); border-color: #d4e2d7; }}
        .section-health .panel {{ background: rgba(251,244,230,0.78); border-color: #ead7b0; }}
        .section-soil .panel {{ background: rgba(244,239,230,0.9); border-color: #d7ccb8; }}
        .panel .row {{ display: flex; flex-wrap: wrap; gap: 8px 24px; }}
        .panel .stat {{ flex: 1; min-width: 130px; }}
        .panel .stat .val {{ font-weight: 600; }}
        .tag {{ display: inline-block; padding: 2px 10px; border-radius: 10px; font-size: var(--font-sm); font-weight: 500; }}
        .tag-routine {{ background: #e8f5e9; color: var(--green); }}
        .tag-monitor {{ background: #fff3e0; color: var(--amber); }}
        .tag-priority {{ background: #fce4ec; color: var(--red); }}
        .tag-elevated {{ background: #fce4ec; color: var(--red); }}
        .tag-high {{ background: #fce4ec; color: var(--red); }}
        .collapsible {{ margin-bottom: 12px; }}
        .collapsible summary {{ cursor: pointer; font-weight: 600; font-size: var(--font-sm); color: var(--accent); padding: 8px 0; }}
        .collapsible[open] summary {{ margin-bottom: 8px; }}
        .accordion {{ border: 1px solid var(--border); border-radius: 6px; overflow: hidden; }}
        .accordion details {{ border-bottom: 1px solid var(--border); }}
        .accordion details:last-child {{ border-bottom: none; }}
        .accordion summary {{ padding: 12px 16px; cursor: pointer; font-weight: 600; font-size: var(--font-sm); background: #f8f5ef; }}
        .accordion summary:hover {{ background: #f2ece1; }}
        .accordion .content {{ padding: 12px 16px; font-size: var(--font-sm); line-height: 1.7; color: #444; }}
        table {{ width: 100%; border-collapse: collapse; font-size: var(--font-sm); }}
        thead {{ background: #efe8dc; }}
        th {{ padding: 10px 8px; text-align: left; font-weight: 600; color: #665b49; font-size: var(--font-sm); position: sticky; top: 0; background: #efe8dc; z-index: 1; }}
        td {{ padding: 8px; border-bottom: 1px solid var(--border); vertical-align: middle; }}
        tr.highlight td {{ background: #fdf5e6; }}
        tr:hover td {{ background: #f8f4ec; }}
        .soil-detail td {{ border-bottom: none; }}
        .detail-grid {{ display: flex; flex-wrap: wrap; gap: 4px 16px; }}
        .detail-grid > span {{ min-width: 100px; font-size: var(--font-sm); }}
        .legend {{ display: flex; gap: 12px; flex-wrap: wrap; font-size: var(--font-sm); color: var(--muted); margin-bottom: 8px; padding: 8px 10px; background: rgba(244,239,230,0.8); border: 1px solid #ddd0bb; border-radius: 8px; }}
        .legend span {{ display: flex; align-items: center; gap: 4px; }}
        .legend .dot {{ width: 12px; height: 12px; border-radius: 2px; display: inline-block; }}
        .view-toggle {{ display: flex; gap: 4px; margin-bottom: 12px; }}
        .view-toggle button {{ padding: 6px 14px; border: 1px solid #d7ccb8; border-radius: 4px; font-size: var(--font-sm); background: #fffdfa; cursor: pointer; color: var(--muted); }}
        .view-toggle button:hover {{ background: #f4efe6; }}
        .view-toggle button.active {{ background: var(--accent-strong); color: #fff; border-color: var(--accent-strong); }}
        @media (min-width: 1024px) {{ .map-rank-grid {{ display: grid; grid-template-columns: 3fr 2fr; gap: 20px; }} }}
        @media (max-width: 1023px) {{ .map-rank-grid {{ grid-template-columns: 1fr; }} }}
        @media (max-width: 768px) {{
            .kpi-row {{ grid-template-columns: repeat(2, 1fr); }}
            .header h1 {{ font-size: 22px; }}
            .container {{ padding: 12px; }}
            .section {{ padding: 16px; }}
        }}
        @media (max-width: 480px) {{ .kpi-row {{ grid-template-columns: 1fr; }} }}
        </style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>{title}</h1>
        <div style="display:flex;flex-wrap:wrap;gap:16px;margin-top:8px;font-size:13px;opacity:0.9;">
            <span><strong>Grower:</strong> {grower_id}</span>
            {f'<span><strong>Year:</strong> {year}</span>' if year else ''}
            <span><strong>Fields:</strong> {field_count}</span>
            {f'<span><strong>Primary Crop:</strong> {primary_crop}</span>' if primary_crop else ''}
            <span style="font-size:12px;opacity:0.7;">Generated: {pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")}</span>
        </div>
    </div>

    <div class="section section-overview">
        <h2>Farm Overview</h2>
        {kpi_html}
        {priority_html}
        <div class="filters">
            <label for="fieldFilterButton">Field:</label>
            <div class="field-multiselect" id="fieldFilterWrap">
                <button id="fieldFilterButton" type="button" onclick="toggleFieldMenu()">All Fields</button>
                <div id="fieldFilterMenu" class="field-menu">
                    <div class="field-menu-actions">
                        <button type="button" onclick="selectAllFields()">Select all</button>
                        <button type="button" onclick="clearFieldSelection()">Clear</button>
                    </div>
                    <div id="fieldFilterOptions"></div>
                </div>
            </div>
            <label for="cropFilter" style="margin-left:8px;">Crop:</label>
            <select id="cropFilter" onchange="applyFieldFilter()">
                <option value="">All Crops</option>
            </select>
            <label for="riskFilter" style="margin-left:8px;">Risk:</label>
            <select id="riskFilter" onchange="applyFieldFilter()">
                <option value="">All Risks</option>
            </select>
            <button class="btn-reset" onclick="resetFilters()">Reset</button>
        </div>
        <div class="map-rank-grid">
            <div class="map-cell">
                <div class="legend" id="riskLegend"></div>
                <div class="chart-container"><div id="mapChart" style="height:450px;"></div></div>
                <p style="font-size:var(--font-sm);margin-top:4px;"><a href="#" onclick="resetMapZoom(); return false;" style="color:var(--accent);">Reset zoom</a></p>
            </div>
            <div class="rank-cell">
                <div class="chart-container"><div id="rankingChart"></div></div>
                <details id="referenceDetails" style="margin-top:8px;">
                    <summary id="referenceSummary" style="font-size:var(--font-sm);cursor:pointer;color:var(--muted);">{ref_summary if ref_summary else 'Reference land-cover areas'}</summary>
                    <div class="chart-container"><div id="refRankingChart"></div></div>
                </details>
            </div>
        </div>
    </div>

    <div class="section section-health">
        <h2>Crop Health & Field Variability</h2>
        <div class="view-toggle" id="cropToggle">
            <button class="active" onclick="switchCropView('condition')">Condition</button>
            <button onclick="switchCropView('stress')">Apparent Stress</button>
        </div>
        <div class="chart-container"><div id="ndviChart"></div></div>
        <div id="selectedFieldPanel"></div>
        <div style="margin-top:16px;">
            <h3>Field Comparison</h3>
            {table_html}
        </div>
    </div>

    <div class="section section-secondary section-weather collapsible">
        <details>
            <summary style="font-size:var(--font-h2);font-weight:600;color:var(--text);cursor:pointer;">Weather & Climate Intelligence</summary>
            <div class="chart-container"><div id="gddPrecipChart"></div></div>
            <div class="chart-container"><div id="weatherChart"></div></div>
        </details>
    </div>

    <div class="section section-soil">
        <h2>Soil Condition Screening & Sustainability</h2>
        <div class="chart-container"><div id="soilChart"></div></div>
        {soil_limits_html}
        <details id="soilDetailsPanel">
            <summary style="font-size:var(--font-sm);cursor:pointer;color:var(--accent);">View field soil details</summary>
            {soil_table_html}
        </details>
    </div>

    <div class="section section-secondary section-provenance collapsible">
        <details>
            <summary style="font-size:var(--font-h2);font-weight:600;color:var(--text);cursor:pointer;">Data Provenance & Integrity</summary>
            <div class="accordion">
                <details>
                    <summary>Integrity notice</summary>
                    <div class="content">
                        <p>The Soil Condition Screening Score is a provisional screening tool based on the specific SSURGO components available for each field, not a comprehensive soil-health assessment. Missing soil components are excluded and remaining weights rescaled. The score is intended for field prioritization and does not substitute for in-field soil sampling.</p>
                        <p style="margin-top:8px;"><strong>AWC scoring plateau:</strong> Available water capacity (AWC) scores are based on thresholds of &lt;3.5 in (0 pts), 3.5-5.0 in (50 pts), and &gt;5.0 in (100 pts). When all fields exceed 5.0 in, all receive 100 on this component. This is accurate behavior — it simply indicates adequate root-zone water holding capacity across all fields — not a data error.</p>
                    </div>
                </details>
                <details>
                    <summary>Available data per field</summary>
                    <div class="content">
                        <div id="provenanceTable"></div>
                    </div>
                </details>
            </div>
        </details>
    </div>

    <div class="section section-secondary section-method collapsible">
        <details>
            <summary style="font-size:var(--font-h2);font-weight:600;color:var(--text);cursor:pointer;">Methodology & Limitations</summary>
            <div class="accordion">
                <details>
                    <summary>Score definitions</summary>
                    <div class="content">
                        <p><strong>Field Intelligence Score (FIS)</strong> = 0.45 × NDVI Condition Score + 0.25 × Soil Condition Screening Score + 0.20 × Weather Suitability Score + 0.10 × Stability Score. Missing components rescale to the available weight total.</p>
                        <p><strong>NDVI Condition Score</strong> = crop-aware greenness score (0-100). Normalised within crop type where sufficient same-crop fields exist; otherwise uses a fixed reference-range transform with per-crop NDVI bounds. Non-row-crop fields are scored separately with wider reference bounds and flagged as lower analytical confidence.</p>
                        <p><strong>Stability Score</strong> = derived from NDVI coefficient of variation (CV), not from mean NDVI. Higher values indicate more stable seasonal vegetation behaviour.</p>
                        <p><strong>Apparent Crop Stress Indicator</strong> = multi-component: low NDVI (45%), NDVI decline from peak (25%), NDVI temporal variability (20%), and weather stress (10%). Higher values indicate greater apparent stress. Remote sensing cannot diagnose the exact cause.</p>
                        <p><strong>Soil Condition Screening Score</strong> = weighted combination of organic matter (30%), pH suitability (20%), available water capacity (20%), drainage class (20%), and CEC (10%). OM/pH/CEC use the 0-30 cm surface depth; AWC uses the 0-100 cm root zone.</p>
                        <p><strong>Conservation Priority Score</strong> = erosion risk (30%), drainage concern (30%), soil condition screening score (25%), and sand content (15%).</p>
                        <p><strong>Weather Suitability Score</strong> = GDD adequacy (40%), precipitation adequacy (30%), dry-spell stress (15%), and extreme heat exposure (15%).</p>
                    </div>
                </details>
                <details>
                    <summary>Important limitations</summary>
                    <div class="content">
                        <ul style="margin-left:16px;">
                            <li>Scores are relative to the fields and data in this dashboard only.</li>
                            <li>NDVI measures greenness, not yield or specific crop health conditions.</li>
                            <li>Weather data is from NASA POWER at ~0.5° grid resolution, not from on-farm stations.</li>
                            <li>Soil data is from SSURGO at 1:12,000 to 1:63,360 scale and may not reflect within-field variability.</li>
                            <li>"Apparent stress" reflects satellite-observed patterns; it is not a diagnosis of pest, nutrient, or disease pressure.</li>
                            <li>Analytical confidence is reported per field based on valid NDVI observation count.</li>
                            <li>These results support prioritisation — they do not replace field scouting or professional judgement.</li>
                            <li>Missing data may influence composite scores. Components with no data are excluded and the remaining weights rescaled.</li>
                        </ul>
                    </div>
                </details>
            </div>
        </details>
    </div>

    {warnings_html}
</div>

<script>
var fieldsData = {json.dumps(fields_data, cls=_NumpyEncoder)};

// --- Short labels map (client-side) ---
var shortLabelsMap = {json.dumps(short_labels, cls=_NumpyEncoder)};
function shortLabel(fid, crop) {{
    var s = shortLabelsMap[fid] || fid;
    return crop ? s + ' · ' + crop : s;
}}

function fieldNumber(fid) {{
    var s = shortLabelsMap[fid] || '';
    var m = s.match(/(\\d+)$/);
    return m ? parseInt(m[1], 10) : 9999;
}}

var dashboardState = {{
    fieldIds: [],
    crop: '',
    risk: '',
    cropView: 'condition',
    expandedFieldId: null,
    expandedSoilFieldId: null,
}};

function syncStateFromDom() {{
    dashboardState.crop = document.getElementById('cropFilter').value;
    dashboardState.risk = document.getElementById('riskFilter').value;
}}

function fieldOptionLabel(field) {{
    return shortLabel(field.field_id) + ' - ' + (field.crop || 'N/A') + ' - ' + field.field_id;
}}

function updateFieldButtonLabel() {{
    var btn = document.getElementById('fieldFilterButton');
    if (!btn) return;
    var ids = dashboardState.fieldIds;
    if (!ids.length) {{
        btn.textContent = 'All Fields';
        return;
    }}
    if (ids.length === 1) {{
        var f = fieldsData.find(function(row) {{ return row.field_id === ids[0]; }});
        btn.textContent = f ? shortLabel(f.field_id, f.crop) : '1 field selected';
        return;
    }}
    if (ids.length === 2) {{
        var labels = ids.map(function(fid) {{
            var f = fieldsData.find(function(row) {{ return row.field_id === fid; }});
            return f ? shortLabel(f.field_id) : fid;
        }});
        btn.textContent = labels.join(', ');
        return;
    }}
    btn.textContent = ids.length + ' fields selected';
}}

function renderFieldCheckboxMenu() {{
    var container = document.getElementById('fieldFilterOptions');
    if (!container) return;
    container.innerHTML = '';
    fieldsData.forEach(function(f) {{
        var label = document.createElement('label');
        label.className = 'field-option';
        var input = document.createElement('input');
        input.type = 'checkbox';
        input.value = f.field_id;
        input.checked = dashboardState.fieldIds.indexOf(f.field_id) !== -1;
        input.onchange = function() {{ toggleFieldSelection(f.field_id, input.checked); }};
        var span = document.createElement('span');
        span.textContent = fieldOptionLabel(f);
        label.appendChild(input);
        label.appendChild(span);
        container.appendChild(label);
    }});
    updateFieldButtonLabel();
}}

function toggleFieldMenu() {{
    var menu = document.getElementById('fieldFilterMenu');
    if (menu) menu.classList.toggle('open');
}}

function closeFieldMenu() {{
    var menu = document.getElementById('fieldFilterMenu');
    if (menu) menu.classList.remove('open');
}}

function toggleFieldSelection(fieldId, checked) {{
    if (checked) {{
        if (dashboardState.fieldIds.indexOf(fieldId) === -1) dashboardState.fieldIds.push(fieldId);
    }} else {{
        dashboardState.fieldIds = dashboardState.fieldIds.filter(function(id) {{ return id !== fieldId; }});
    }}
    renderFieldCheckboxMenu();
    applyFieldFilter();
}}

function selectAllFields() {{
    dashboardState.fieldIds = fieldsData.map(function(f) {{ return f.field_id; }});
    renderFieldCheckboxMenu();
    applyFieldFilter();
}}

function clearFieldSelection() {{
    dashboardState.fieldIds = [];
    renderFieldCheckboxMenu();
    applyFieldFilter();
}}

function rowCropFields(fields) {{
    return fields.filter(function(f) {{ return f.row_crop_fis_score != null; }});
}}

function referenceFields(fields) {{
    return fields.filter(function(f) {{ return f.row_crop_fis_score == null; }});
}}

// --- Central filter ---
function getFilteredFields() {{
    syncStateFromDom();
    var fieldIds = dashboardState.fieldIds;
    var cropId = dashboardState.crop;
    var riskId = dashboardState.risk;
    return fieldsData.filter(function(f) {{
        return (!fieldIds.length || fieldIds.indexOf(f.field_id) !== -1) &&
               (cropId === '' || f.crop === cropId) &&
               (riskId === '' || f.risk_category === riskId);
    }});
}}

function noDataHtml() {{
    return '<div style="padding:60px 20px;text-align:center;color:#999;font-size:14px;border:1px dashed #ddd;border-radius:8px;margin:8px 0;">No data available.</div>';
}}

function safePlot(divId, traces, layout, extra) {{
    if (traces.length > 0) {{
        Plotly.newPlot(divId, traces, layout, extra || {{responsive: true}});
    }} else {{
        document.getElementById(divId).innerHTML = noDataHtml();
    }}
}}

// --- KPI builder ---
function buildKpiHtml(filtered) {{
    var rc = rowCropFields(filtered);
    var ref = referenceFields(filtered);
    var nRc = rc.length;
    if (nRc === 0 && filtered.length === 1) {{
        var f = filtered[0];
        return '<div class="kpi-row"><div class="kpi-card"><div class="value">' + shortLabel(f.field_id, f.crop) + '</div><div class="label">Reference area — no Row-Crop FIS</div></div><div class="kpi-card"><div class="value">' + ((f.ndvi_condition_score != null) ? f.ndvi_condition_score.toFixed(0) + '/100' : '&mdash;') + '</div><div class="label">Reference vegetation condition</div></div></div>';
    }}
    if (nRc === 0) {{
        return '<div class="kpi-row"><div class="kpi-card"><div class="value">&mdash;</div><div class="label">No matching row-crop fields</div></div><div class="kpi-card"><div class="value">&mdash;</div><div class="label">Row-crop acres</div></div><div class="kpi-card"><div class="value">&mdash;</div><div class="label">Mean Row-Crop FIS</div></div><div class="kpi-card"><div class="value">&mdash;</div><div class="label">Fields needing attention</div></div><div class="kpi-card"><div class="value">' + (ref.length ? ((ref.filter(function(f) {{ return f.ndvi_condition_score != null; }}).reduce(function(s,f) {{ return s + (f.ndvi_condition_score ?? 0); }},0) / Math.max(1, ref.filter(function(f) {{ return f.ndvi_condition_score != null; }}).length)).toFixed(0) + '/100') : '&mdash;') + '</div><div class="label">Reference vegetation condition</div></div></div>';
    }}
    var rcAcres = rc.reduce(function(s, f) {{ return s + (f.area_acres ?? 0); }}, 0);
    var rcFisVals = rc.map(function(f) {{ return f.row_crop_fis_score; }}).filter(function(v) {{ return v != null; }});
    var meanRcFis = rcFisVals.length ? rcFisVals.reduce(function(a, b) {{ return a + b; }}, 0) / rcFisVals.length : 0;
    var nAttn = rcFisVals.filter(function(v) {{ return v < 50; }}).length;
    var ndviVals = rc.map(function(f) {{ return f.ndvi_condition_score; }}).filter(function(v) {{ return v != null; }});
    var meanNdvi = ndviVals.length ? ndviVals.reduce(function(a, b) {{ return a + b; }}, 0) / ndviVals.length : 0;
    var attnCls = nAttn === 0 ? 'attention ok' : 'attention';
    return '<div class="kpi-row">' +
        '<div class="kpi-card"><div class="value">' + nRc + '</div><div class="label">Row-crop fields</div></div>' +
        '<div class="kpi-card"><div class="value">' + rcAcres.toFixed(0) + '</div><div class="label">Row-crop acres</div></div>' +
        '<div class="kpi-card"><div class="value">' + meanRcFis.toFixed(0) + '/100</div><div class="label">Mean Row-Crop FIS</div></div>' +
        '<div class="kpi-card ' + attnCls + '"><div class="value">' + nAttn + '</div><div class="label">Fields needing attention</div></div>' +
        '<div class="kpi-card"><div class="value">' + meanNdvi.toFixed(0) + '/100</div><div class="label">Mean Crop Condition</div></div>' +
        '</div>';
}}

// --- Metadata line builder ---
function buildMetadataLine(filtered) {{
    if (filtered.length === 1) {{
        var f = filtered[0];
        return shortLabel(f.field_id, f.crop) + ' · ' + ((f.area_acres ?? 0).toFixed(0)) + ' ac · Full ID: ' + f.field_id;
    }}
    var rc = filtered.filter(function(f) {{ return f.row_crop_fis_score != null; }});
    var ref = filtered.filter(function(f) {{ return f.row_crop_fis_score == null; }});
    var crops = new Set();
    filtered.forEach(function(f) {{ if (f.crop) crops.add(f.crop); }});
    if (crops.size === 1) {{
        var c = crops.values().next().value;
        var cAcres = filtered.reduce(function(s, f) {{ return s + (f.crop === c ? (f.area_acres ?? 0) : 0); }}, 0);
        return c + ' fields: ' + filtered.length + ' · Total ' + c.toLowerCase() + ' acreage: ' + cAcres.toFixed(0) + ' ac';
    }}
    return 'Total fields: ' + filtered.length + ' · Row-crop fields: ' + rc.length + ' · Others: ' + ref.length;
}}

// --- Priority panel builder ---
function buildPriorityHtml(filtered) {{
    var rc = rowCropFields(filtered);
    if (rc.length === 0 && filtered.length === 1) {{
        return '<div class="panel" id="priorityPanel"><div class="row"><div class="stat">Reference area — not scored for row-crop FIS.</div></div></div>';
    }}
    if (rc.length === 0) {{
        return '<div class="panel" id="priorityPanel"><div class="row"><div class="stat">No row-crop fields in current selection.</div></div></div>';
    }}
    var sorted = rc.slice().sort(function(a, b) {{ return (b.row_crop_fis_score ?? 0) - (a.row_crop_fis_score ?? 0); }});
    var topF = sorted[0];
    var botF = sorted[sorted.length - 1];
    var fisVals = rc.map(function(f) {{ return f.row_crop_fis_score; }}).filter(function(v) {{ return v != null; }});
    var meanFis = fisVals.length ? fisVals.reduce(function(a, b) {{ return a + b; }}, 0) / fisVals.length : 0;
    var nAttn = fisVals.filter(function(v) {{ return v < 50; }}).length;
    var condMsg = meanFis >= 65 ? 'favourable' : meanFis >= 50 ? 'moderate' : 'needs attention';
    var drivers = [];
    if (botF) {{
        var issues = [];
        if ((botF.crop_stress ?? 0) > 50) issues.push('high apparent stress');
        if ((botF.ndvi_condition_score ?? 100) < 50) issues.push('very low crop condition');
        if (issues.length) drivers.push(shortLabel(botF.field_id, botF.crop) + ' shows ' + issues.join('; ') + '.');
    }}
    var attnLabel = nAttn === 1 ? '1 field' : nAttn + ' fields';
    var attnText = nAttn === 1 ? 'One field has an FIS value below 50.' : nAttn > 1 ? nAttn + ' fields have FIS values below 50.' : 'All row-crop fields are above threshold.';
    return '<div class="panel" id="priorityPanel">' +
        '<div class="row">' +
        '<div class="stat"><span class="val">Best:</span> ' + shortLabel(topF.field_id, topF.crop) + '</div>' +
        '<div class="stat"><span class="val">Lowest:</span> ' + shortLabel(botF.field_id, botF.crop) + ' (' + (botF.row_crop_fis_score ?? 0).toFixed(0) + ' FIS)</div>' +
        '<div class="stat"><span class="val">Needs attention:</span> ' + attnLabel + '</div>' +
        '</div>' +
        '<div style="margin-top:8px;font-size:var(--font-sm);color:var(--muted);">Overall row-crop condition is ' + condMsg + '. ' + attnText +
        (drivers.length ? ' ' + drivers.join(' ') : '') +
        '</div></div>';
}}

// --- NDVI chart trace builders ---
function buildCropTraces(filtered) {{
    var rc = rowCropFields(filtered);
    var data = rc.length ? rc : filtered;
    var sorted = data.slice().sort(function(a, b) {{ return (b.ndvi_condition_score ?? 0) - (a.ndvi_condition_score ?? 0); }});
    var labels = sorted.map(function(f) {{ return shortLabel(f.field_id, f.crop); }});
    var condVals = sorted.map(function(f) {{ return f.ndvi_condition_score ?? 0; }});
    var stressVals = sorted.map(function(f) {{ return f.crop_stress ?? 0; }});
    function cColor(v) {{ return v >= 65 ? '#2e7d32' : v >= 50 ? '#ff8f00' : '#b71c1c'; }}
    function sColor(v) {{ return v >= 60 ? '#b71c1c' : v >= 40 ? '#ff8f00' : '#2e7d32'; }}
    var condTrace = [{{ type: 'bar', x: condVals, y: labels, orientation: 'h',
        marker: {{color: condVals.map(cColor)}}, textposition: 'none',
        hovertemplate: '<b>%{{y}}</b><br>NDVI Condition: %{{x:.0f}}/100<extra></extra>' }}];
    var stressTrace = [{{ type: 'bar', x: stressVals, y: labels, orientation: 'h',
        marker: {{color: stressVals.map(sColor)}}, textposition: 'none',
        hovertemplate: '<b>%{{y}}</b><br>Apparent Stress: %{{x:.0f}}/100<extra></extra>' }}];
    return {{cond: condTrace, stress: stressTrace}};
}}

// --- Ranking chart builders ---
function buildRankingTraces(filtered) {{
    var rc = rowCropFields(filtered);
    var sorted = rc.slice().sort(function(a, b) {{ return (b.fis ?? 0) - (a.fis ?? 0); }});
    if (sorted.length === 0) return null;
    var labels = sorted.map(function(f) {{ return shortLabel(f.field_id, f.crop); }});
    var vals = sorted.map(function(f) {{ return f.fis ?? 0; }});
    var colors = sorted.map(function(f) {{ var r = f.risk_category || ''; return r === 'Low' ? '#2e7d32' : r === 'Moderate' ? '#e65100' : r === 'Elevated' ? '#b71c1c' : r === 'High' ? '#b71c1c' : '#7f7f7f'; }});
    var cust = sorted.map(function(f) {{ return [f.area_acres ?? 0, f.risk_category || '', f.ndvi_condition_score ?? 0, f.soil_condition_screening_score ?? 0]; }});
    return [{{
        type: 'bar', x: vals, y: labels, orientation: 'h',
        marker: {{color: colors}}, textposition: 'none',
        customdata: cust,
        hovertemplate: '<b>%{{y}}</b><br>FIS: %{{x:.1f}}/100<br>Acreage: %{{customdata[0]:.0f}} ac<br>Risk: %{{customdata[1]}}<br>Crop condition: %{{customdata[2]:.0f}}/100<br>Soil screening: %{{customdata[3]:.0f}}/100<extra></extra>',
    }}];
}}

// --- Filter table rows ---
function closeAllDetailRows() {{
    dashboardState.expandedFieldId = null;
    dashboardState.expandedSoilFieldId = null;
    document.querySelectorAll('.field-detail').forEach(function(row) {{ row.style.display = 'none'; }});
    document.querySelectorAll('.soil-detail').forEach(function(row) {{ row.style.display = 'none'; }});
}}

function filterTableRows(filtered) {{
    var ids = new Set(filtered.map(function(f) {{ return f.field_id; }}));
    document.querySelectorAll('.field-primary').forEach(function(row) {{
        var match = ids.has(row.getAttribute('data-field-id'));
        row.style.display = match ? '' : 'none';
    }});
    document.querySelectorAll('.field-detail').forEach(function(row) {{
        var match = ids.has(row.getAttribute('data-field-id'));
        row.style.display = (match && dashboardState.expandedFieldId === row.getAttribute('data-field-id')) ? 'table-row' : 'none';
    }});
}}

function filterSoilRows(filtered) {{
    var ids = new Set(filtered.map(function(f) {{ return f.field_id; }}));
    document.querySelectorAll('.soil-primary').forEach(function(row) {{
        var match = ids.has(row.getAttribute('data-soil-field-id'));
        row.style.display = match ? '' : 'none';
    }});
    document.querySelectorAll('.soil-detail').forEach(function(row) {{
        var match = ids.has(row.getAttribute('data-soil-field-id'));
        row.style.display = (match && dashboardState.expandedSoilFieldId === row.getAttribute('data-soil-field-id')) ? 'table-row' : 'none';
    }});
}}

function buildReferenceSummary(filtered) {{
    var ref = referenceFields(filtered);
    if (!ref.length) return 'No reference areas in current selection';
    var names = Array.from(new Set(ref.map(function(f) {{ return f.crop; }})));
    var joined = names.length === 1 ? names[0] : names.length === 2 ? names[0] + ' and ' + names[1] : names.join(', ');
    return ref.length + (ref.length === 1 ? ' reference area: ' : ' reference areas: ') + joined;
}}

function buildReferenceTraces(filtered) {{
    var ref = referenceFields(filtered).sort(function(a, b) {{ return (b.fis ?? 0) - (a.fis ?? 0); }});
    if (!ref.length) return [];
    return [{{
        type: 'bar',
        x: ref.map(function(f) {{ return f.fis ?? 0; }}),
        y: ref.map(function(f) {{ return shortLabel(f.field_id, f.crop); }}),
        orientation: 'h',
        marker: {{color: '#7f7f7f'}},
        hovertemplate: '<b>%{{y}}</b><br>FIS: %{{x:.1f}}/100<extra></extra>',
    }}];
}}

function buildSoilSummary(filtered) {{
    if (filtered.length === 1) {{
        var f = filtered[0];
        var issues = [];
        if ((f.om_score ?? 100) < 40) issues.push('low organic matter');
        if ((f.drainage_score ?? 100) < 40) issues.push((f.drainage_class || 'drainage limitations').toLowerCase());
        if ((f.awc_score ?? 100) < 40) issues.push('low available water capacity');
        if ((f.cec_score ?? 100) < 40) issues.push('low CEC');
        return issues.length ? 'Main soil limitations: ' + issues.slice(0, 2).join(' and ') + '.' : 'No major soil screening limitations are evident for the selected field.';
    }}
    var defs = [
        ['low organic matter', function(f) {{ return (f.om_score ?? 100) < 40; }}],
        ['poor drainage', function(f) {{ return (f.drainage_score ?? 100) < 40; }}],
        ['low available water capacity', function(f) {{ return (f.awc_score ?? 100) < 40; }}],
        ['low CEC', function(f) {{ return (f.cec_score ?? 100) < 40; }}],
    ];
    var counts = defs.map(function(d) {{
        return [d[0], filtered.filter(function(f) {{ return d[1](f); }}).length];
    }}).filter(function(x) {{ return x[1] > 0; }}).sort(function(a, b) {{ return b[1] - a[1]; }});
    if (!counts.length) return 'No significant soil screening limitations identified among the current fields.';
    var parts = counts.slice(0, 2).map(function(x) {{ return x[0] + ' in ' + x[1] + ' of ' + filtered.length + ' fields'; }});
    return 'Common screening limitations: ' + (parts.length === 1 ? parts[0] : parts[0] + ' and ' + parts[1]) + '.';
}}

// --- Map helpers ---
var initialMapLayout = null;

function fitMapToFields(filtered) {{
    if (filtered.length === 0) return;
    if (filtered.length === 1) {{
        var fid = filtered[0].field_id;
        var allLons = [], allLats = [];
        for (var t = 0; t < originalMapTrace.length; t++) {{
            if (originalMapTrace[t].name === fid) {{
                Array.prototype.push.apply(allLons, originalMapTrace[t].lon);
                Array.prototype.push.apply(allLats, originalMapTrace[t].lat);
            }}
        }}
        if (allLons.length < 2) return;
        var minLon = Math.min.apply(null, allLons);
        var maxLon = Math.max.apply(null, allLons);
        var minLat = Math.min.apply(null, allLats);
        var maxLat = Math.max.apply(null, allLats);
        Plotly.relayout('mapChart', {{
            'mapbox.center': {{lat: (minLat + maxLat) / 2, lon: (minLon + maxLon) / 2}},
            'mapbox.zoom': 14
        }});
    }} else {{
        var ids = new Set(filtered.map(function(f) {{ return f.field_id; }}));
        var allLons = [], allLats = [];
        for (var t = 0; t < originalMapTrace.length; t++) {{
            if (ids.has(originalMapTrace[t].name)) {{
                Array.prototype.push.apply(allLons, originalMapTrace[t].lon || []);
                Array.prototype.push.apply(allLats, originalMapTrace[t].lat || []);
            }}
        }}
        if (allLons.length < 2 || !initialMapLayout) return;
        var minLon = Math.min.apply(null, allLons);
        var maxLon = Math.max.apply(null, allLons);
        var minLat = Math.min.apply(null, allLats);
        var maxLat = Math.max.apply(null, allLats);
        var centerLat = (minLat + maxLat) / 2;
        var centerLon = (minLon + maxLon) / 2;
        var lonSpan = Math.max((maxLon - minLon) * 1.25, 0.001);
        var latSpan = Math.max((maxLat - minLat) * 1.25, 0.001);
        var maxSpan = Math.max(lonSpan, latSpan * Math.cos(centerLat * Math.PI / 180));
        var zoom = Math.max(10, Math.min(16, 13 - Math.log2(maxSpan * 100)));
        Plotly.relayout('mapChart', {{
            'mapbox.center': {{lat: centerLat, lon: centerLon}},
            'mapbox.zoom': zoom
        }});
    }}
}}

function buildMapTraces(filtered) {{
    var ids = new Set(filtered.map(function(f) {{ return f.field_id; }}));
    var single = filtered.length === 1 ? filtered[0].field_id : null;
    return originalMapTrace.map(function(trace) {{
        var match = ids.has(trace.name);
        var out = JSON.parse(JSON.stringify(trace));
        out.visible = match;
        if (single && trace.name === single) {{
            out.line.width = 4;
            if (out.fillcolor && out.fillcolor.length >= 7) out.fillcolor = out.fillcolor.slice(0, 7) + '90';
        }} else {{
            out.line.width = 2;
            if (out.fillcolor && out.fillcolor.length >= 7) out.fillcolor = out.fillcolor.slice(0, 7) + '60';
        }}
        return out;
    }});
}}

// --- Show no-results ---
function showNoResults() {{
    var kpiRow = document.querySelector('.kpi-row');
    if (kpiRow) kpiRow.outerHTML = '<div class="kpi-row"><div class="kpi-card"><div class="value">&mdash;</div><div class="label">No matching row-crop fields</div></div><div class="kpi-card"><div class="value">&mdash;</div><div class="label">Row-crop acres</div></div><div class="kpi-card"><div class="value">&mdash;</div><div class="label">Mean Row-Crop FIS</div></div><div class="kpi-card"><div class="value">&mdash;</div><div class="label">Fields needing attention</div></div><div class="kpi-card"><div class="value">&mdash;</div><div class="label">Mean Crop Condition</div></div></div>';
    document.querySelector('.metadata-line').textContent = 'No fields match the selected filters.';
    document.getElementById('priorityPanel').innerHTML = '<div class="row"><div class="stat">No fields match the selected filters.</div></div>';
    document.getElementById('rankingChart').innerHTML = noDataHtml();
    document.getElementById('referenceSummary').textContent = '';
    document.getElementById('referenceDetails').style.display = 'none';
    document.getElementById('refRankingChart').innerHTML = '';
    document.getElementById('selectedFieldPanel').innerHTML = '';
    Plotly.react('ndviChart', [], {{height: 100}});
    Plotly.react('soilChart', [], {{height: 100}});
    Plotly.react('gddPrecipChart', [], {{height: 100}});
    Plotly.react('weatherChart', [], {{height: 100}});
    document.getElementById('soilSummary').textContent = 'No fields match the selected filters.';
    closeAllDetailRows();
    filterTableRows([]);
    filterSoilRows([]);
    Plotly.react('mapChart', buildMapTraces([]), mapLayout);
    document.getElementById('soilDetailsPanel').open = false;
    document.getElementById('referenceDetails').open = false;
}}

// --- Main filter orchestrator ---
function applyFieldFilter() {{
    var filtered = getFilteredFields();
    closeAllDetailRows();

    if (filtered.length === 0) {{
        window._ndviFiltered = null;
        showNoResults();
        return;
    }}

    // Update KPI
    var kpiRow = document.querySelector('.kpi-row');
    if (kpiRow) kpiRow.outerHTML = buildKpiHtml(filtered);

    // Update metadata line
    var metaLine = document.querySelector('.metadata-line');
    if (metaLine) metaLine.textContent = buildMetadataLine(filtered);

    // Update priority panel
    document.getElementById('priorityPanel').outerHTML = buildPriorityHtml(filtered);

    // Ranking chart
    var rc = rowCropFields(filtered);
    var ref = referenceFields(filtered);
    if (filtered.length === 1 && rc.length === 0) {{
        // Single reference area selected
        var rf = filtered[0];
        document.getElementById('rankingChart').innerHTML = noDataHtml();
        Plotly.react('rankingChart', [{{
            type: 'bar', x: [rf.fis ?? 0], y: [shortLabel(rf.field_id, rf.crop)],
            orientation: 'h', marker: {{color: '#7f7f7f'}},
            hovertemplate: '<b>%{{y}}</b><br>FIS: %{{x:.1f}}/100<extra></extra>',
        }}], {{
            title: {{text: 'Reference Area — FIS'}}, xaxis: {{title: 'FIS (0-100)', range: [0, 105]}},
            height: 150, margin: {{l: 140, r: 20, t: 40, b: 40}},
        }});
    }} else if (filtered.length === 1) {{
        var ff = filtered[0];
        Plotly.react('rankingChart', [{{
            type: 'bar', x: [ff.fis ?? 0], y: [shortLabel(ff.field_id, ff.crop)],
            orientation: 'h', marker: {{color: (ff.fis ?? 0) >= 50 ? '#2e7d32' : '#b71c1c'}},
            hovertemplate: '<b>%{{y}}</b><br>FIS: %{{x:.0f}}/100<extra></extra>',
        }}], {{
            title: {{text: 'Field Intelligence Score'}},
            xaxis: {{title: 'FIS (0-100)', range: [0, 105]}},
            height: 150, margin: {{l: 140, r: 20, t: 40, b: 40}},
        }});
    }} else if (rc.length > 0) {{
        var rankTraces = buildRankingTraces(filtered);
        if (rankTraces) {{
            Plotly.react('rankingChart', rankTraces, {{
                title: {{text: 'Row-Crop FIS Ranking', font: {{size: 15}}}},
                xaxis: {{title: 'FIS (0-100)', range: [0, 105], dtick: 10}},
                yaxis: {{title: '', automargin: true}},
                height: Math.max(150, rc.length * 36),
                margin: {{l: 140, r: 20, t: 40, b: 40}}, bargap: 0.35,
            }});
        }}
    }}

    // Reference chart visibility
    document.getElementById('referenceSummary').textContent = buildReferenceSummary(filtered);
    document.getElementById('referenceDetails').style.display = ref.length ? '' : 'none';
    if (ref.length) {{
        Plotly.react('refRankingChart', buildReferenceTraces(filtered), refLayout);
    }} else {{
        document.getElementById('refRankingChart').innerHTML = '';
    }}

    // NDVI chart
    var traces = buildCropTraces(filtered);
    window._ndviFiltered = traces;
    switchCropView(dashboardState.cropView);

    // Soil chart
    if (filtered.length === 1) {{
        var sf = filtered[0];
        var sc = sf.soil_condition_screening_score ?? 0;
        var scolor = sc >= 80 ? '#1b5e20' : sc >= 65 ? '#2e7d32' : sc >= 50 ? '#ff8f00' : sc >= 35 ? '#e65100' : '#b71c1c';
        Plotly.react('soilChart', [{{
            type: 'bar', x: [shortLabel(sf.field_id, sf.crop)], y: [sc],
            marker: {{color: scolor}},
            hovertemplate: '<b>%{{x}}</b><br>Soil Score: %{{y:.0f}}<extra></extra>',
        }}], {{
            title: {{text: 'Soil Condition Screening Score'}},
            yaxis: {{title: 'Score (0-100)', range: [0, 105]}},
            height: 300, margin: {{t: 40, b: 80, l: 60, r: 20}},
        }});
    }} else {{
        var orderedSoil = filtered.slice().sort(function(a, b) {{ return fieldNumber(a.field_id) - fieldNumber(b.field_id); }});
        var soilLabels = orderedSoil.map(function(f) {{ return shortLabel(f.field_id); }});
        var soilValues = orderedSoil.map(function(f) {{ return f.soil_condition_screening_score ?? 0; }});
        var soilColors = orderedSoil.map(function(f) {{
            var v = f.soil_condition_screening_score ?? 0;
            return v >= 80 ? '#1b5e20' : v >= 65 ? '#2e7d32' : v >= 50 ? '#ff8f00' : v >= 35 ? '#e65100' : '#b71c1c';
        }});
        Plotly.react('soilChart', [{{
            type: 'bar', x: soilLabels, y: soilValues,
            marker: {{color: soilColors}},
            textposition: 'none',
            text: orderedSoil.map(function(f) {{ return (f.crop || 'N/A') + ' · ' + f.field_id + ' · ' + (f.soil_screening_category || ''); }}),
            hovertemplate: '<b>%{{x}}</b><br>Soil Condition Screening Score: %{{y:.1f}}<br>%{{text}}<extra></extra>',
        }}], soilLayout);
    }}

    // Field table
    filterTableRows(filtered);

    // Soil table
    filterSoilRows(filtered);
    document.getElementById('soilSummary').textContent = buildSoilSummary(filtered);

    // Map visibility
    var filteredIds = new Set();
    filtered.forEach(function(f) {{ filteredIds.add(f.field_id); }});
    Plotly.react('mapChart', buildMapTraces(filtered), mapLayout);
    fitMapToFields(filtered);

    // GDD/Precip filter
    var gddUpdate = {{visible: []}};
    for (var t = 0; t < gddPrecipTrace.length; t++) {{
        var gname = gddPrecipTrace[t].name || '';
        var gfid = gname.replace(' GDD', '').replace(' Precip', '');
        gddUpdate.visible.push(filteredIds.has(gfid) || gfid === '');
    }}
    Plotly.update('gddPrecipChart', gddUpdate, {{}});

    // Weather chart filter (fix: explicit name/x extraction)
    var wUpdate = {{visible: []}};
    for (var t = 0; t < weatherTrace.length; t++) {{
        var wt = weatherTrace[t];
        var wfid = wt.name ? wt.name : (wt.x && wt.x.length ? wt.x[0] : '');
        wUpdate.visible.push(filteredIds.has(wfid) || wfid === '');
    }}
    Plotly.update('weatherChart', wUpdate, {{}});

    // Selected field panel (single field)
    var panel = document.getElementById('selectedFieldPanel');
    if (filtered.length === 1) {{
        var ff = filtered[0];
        var fis = ff.row_crop_fis_score != null ? ff.row_crop_fis_score : ff.fis;
        fis = fis != null ? fis.toFixed(0) + '/100' : 'N/A';
        var interp = 'Prioritize field scouting to determine whether the vegetation signal relates to establishment, moisture, nutrient, pest or disease factors.';
        panel.innerHTML = '<div class="panel" style="margin-top:8px;">' +
            '<div class="row"><div class="stat"><span class="val">' + shortLabel(ff.field_id, ff.crop) + '</span> · ' + ((ff.area_acres ?? 0).toFixed(0)) + ' ac</div>' +
            '<div class="stat">FIS: ' + fis + '</div>' +
            '<div class="stat">Crop Condition: ' + (ff.ndvi_condition_score != null ? ff.ndvi_condition_score.toFixed(0) + '/100' : 'N/A') + '</div>' +
            '<div class="stat">Apparent Stress: ' + (ff.crop_stress != null ? ff.crop_stress.toFixed(0) + '/100' : 'N/A') + '</div>' +
            '<div class="stat">Soil Condition: ' + (ff.soil_condition_screening_score != null ? ff.soil_condition_screening_score.toFixed(0) + '/100' : 'N/A') + '</div>' +
            '<div class="stat">Conservation Priority: ' + (ff.conservation_priority != null ? ff.conservation_priority.toFixed(0) + '/100' : 'N/A') + '</div>' +
            '<div class="stat">Risk: ' + (ff.risk_category || 'N/A') + '</div>' +
            '<div class="stat">Confidence: ' + (ff.fis_confidence || 'N/A') + '</div>' +
            '</div><div style="margin-top:8px;font-size:var(--font-sm);color:var(--muted);">' + shortLabel(ff.field_id, ff.crop) + ' has ' + ((ff.ndvi_condition_score ?? 100) < 50 ? 'very low crop condition' : 'moderate crop condition') + ' and ' + ((ff.crop_stress ?? 0) > 60 ? 'high apparent stress' : 'moderate apparent stress') + ', while its soil condition is ' + ((ff.soil_condition_screening_score ?? 0) >= 65 ? 'strong' : (ff.soil_condition_screening_score ?? 0) >= 50 ? 'moderate' : 'constrained') + '. ' + interp + '</div></div>';
    }} else {{
        panel.innerHTML = '';
    }}
}}

// --- Crop health toggle ---
function switchCropView(view) {{
    dashboardState.cropView = view;
    var btns = document.querySelectorAll('#cropToggle button');
    btns.forEach(function(b) {{ b.classList.remove('active'); }});
    if (view === 'condition') btns[0].classList.add('active');
    else btns[1].classList.add('active');

    if (window._ndviFiltered) {{
        Plotly.react('ndviChart', dashboardState.cropView === 'condition' ? window._ndviFiltered.cond : window._ndviFiltered.stress, ndviLayout);
    }} else {{
        Plotly.react('ndviChart', dashboardState.cropView === 'condition' ? ndviCondTrace : ndviStressTrace, ndviLayout);
    }}
}}

// --- Reset filters ---
function resetFilters() {{
    closeFieldMenu();
    document.getElementById('cropFilter').value = '';
    document.getElementById('riskFilter').value = '';
    dashboardState.fieldIds = [];
    dashboardState.crop = '';
    dashboardState.risk = '';
    dashboardState.cropView = 'condition';
    closeAllDetailRows();
    renderFieldCheckboxMenu();

    // Restore initial state
    var kpiParent = document.querySelector('.kpi-row');
    if (kpiParent) kpiParent.outerHTML = initialKpiHtml;
    document.querySelector('.metadata-line').textContent = initialMetadataText;
    document.getElementById('priorityPanel').outerHTML = initialPriorityHtml;

    Plotly.react('rankingChart', fisTrace, fisLayout);
    Plotly.react('refRankingChart', refTrace, refLayout);
    Plotly.react('soilChart', soilTrace, soilLayout);
    Plotly.react('ndviChart', ndviCondTrace, ndviLayout);
    Plotly.react('gddPrecipChart', gddPrecipTrace, gddPrecipLayout);
    Plotly.react('weatherChart', weatherTrace, weatherLayout);
    window._ndviFiltered = null;

    // Restore map
    if (initialMapLayout) {{
        Plotly.relayout('mapChart', {{
            'mapbox.center': initialMapLayout.mapbox.center,
            'mapbox.zoom': initialMapLayout.mapbox.zoom
        }});
    }}
    Plotly.react('mapChart', originalMapTrace, mapLayout);

    // Restore tables
    document.querySelectorAll('.field-primary').forEach(function(row) {{ row.style.display = ''; }});
    document.querySelectorAll('.soil-primary').forEach(function(row) {{ row.style.display = ''; }});
    document.querySelectorAll('.field-detail').forEach(function(row) {{ row.style.display = 'none'; }});
    document.querySelectorAll('.soil-detail').forEach(function(row) {{ row.style.display = 'none'; }});
    document.getElementById('referenceSummary').textContent = buildReferenceSummary(fieldsData);
    document.getElementById('referenceDetails').style.display = '';
    document.getElementById('referenceDetails').open = false;
    document.getElementById('soilDetailsPanel').open = false;
    document.getElementById('soilSummary').textContent = buildSoilSummary(fieldsData);

    document.getElementById('selectedFieldPanel').innerHTML = '';
    switchCropView('condition');
}}

var initialKpiHtml = '', initialMetadataText = '', initialPriorityHtml = '';

// --- Initial plots ---
var fisTrace = {fis_traces};
var fisLayout = {fis_layout};
safePlot('rankingChart', fisTrace, fisLayout);

var refTrace = {ref_traces};
var refLayout = {ref_layout};
safePlot('refRankingChart', refTrace, refLayout);

var mapTrace = {map_traces};
var originalMapTrace = JSON.parse(JSON.stringify(mapTrace));
var mapLayout = {map_layout};
safePlot('mapChart', mapTrace, mapLayout);
initialMapLayout = JSON.parse(JSON.stringify(mapLayout));

// Risk legend
(function() {{
    var legend = document.getElementById('riskLegend');
    if (!legend) return;
    var items = [
        {{label: 'Low', color: '#2e7d32'}}, {{label: 'Moderate', color: '#e65100'}},
        {{label: 'Elevated', color: '#b71c1c'}}, {{label: 'High', color: '#b71c1c'}},
    ];
    items.forEach(function(it) {{
        var span = document.createElement('span');
        span.innerHTML = '<span class="dot" style="background:' + it.color + ';"></span> ' + it.label;
        legend.appendChild(span);
    }});
}})();

function resetMapZoom() {{
    if (initialMapLayout) {{
        Plotly.relayout('mapChart', {{
            'mapbox.center': initialMapLayout.mapbox.center,
            'mapbox.zoom': initialMapLayout.mapbox.zoom
        }});
    }}
}}

function computeFieldZoom(fieldName) {{
    var allLons = [], allLats = [];
    for (var t = 0; t < originalMapTrace.length; t++) {{
        if (originalMapTrace[t].name === fieldName) {{
            Array.prototype.push.apply(allLons, originalMapTrace[t].lon);
            Array.prototype.push.apply(allLats, originalMapTrace[t].lat);
        }}
    }}
    if (allLons.length < 2) return null;
    var minLon = Math.min.apply(null, allLons);
    var maxLon = Math.max.apply(null, allLons);
    var minLat = Math.min.apply(null, allLats);
    var maxLat = Math.max.apply(null, allLats);
    return {{
        center: {{lat: (minLat + maxLat) / 2, lon: (minLon + maxLon) / 2}},
        zoom: 14
    }};
}}

document.getElementById('mapChart').on('plotly_click', function(data) {{
    if (!data.points || !data.points.length) return;
    var clickedName = data.points[0].data.name;
    if (!clickedName) return;
    dashboardState.fieldIds = [clickedName];
    renderFieldCheckboxMenu();
    closeFieldMenu();
    applyFieldFilter();
}});

var ndviLayout = {ndvi_layout};
var ndviCondTrace = {ndvi_cond_trace};
var ndviStressTrace = {ndvi_stress_trace};
safePlot('ndviChart', ndviCondTrace, ndviLayout);

var gddPrecipTrace = {gdd_precip_traces};
var gddPrecipLayout = {gdd_precip_layout};
safePlot('gddPrecipChart', gddPrecipTrace, gddPrecipLayout);

var weatherTrace = {weather_traces};
var weatherLayout = {weather_layout};
safePlot('weatherChart', weatherTrace, weatherLayout);

var soilTrace = {soil_traces};
var soilLayout = {soil_layout};
safePlot('soilChart', soilTrace, soilLayout);
initialKpiHtml = document.querySelector('.kpi-row').outerHTML;
initialMetadataText = document.querySelector('.metadata-line').textContent;
initialPriorityHtml = document.getElementById('priorityPanel').outerHTML;

// Provenance table
(function() {{
    var provHtml = '<table><thead><tr><th>Field</th><th style="text-align:center;">OM</th><th style="text-align:center;">pH</th><th style="text-align:center;">CEC</th><th style="text-align:center;">AWC</th><th style="text-align:center;">Drainage</th><th style="text-align:center;">Erosion</th><th style="text-align:center;">Map Unit</th><th style="text-align:center;">Coverage</th></tr></thead><tbody>';
    for (var i = 0; i < fieldsData.length; i++) {{
        var f = fieldsData[i];
        function chk(v) {{ return (v !== null && v !== undefined && v !== '' && !(typeof v === 'number' && isNaN(v))) ? '<span style="color:#2e7d32;">&#10003;</span>' : '<span style="color:#999;">&mdash;</span>'; }}
        provHtml += '<tr><td>' + f.field_id + '</td>'
            + '<td style="text-align:center;">' + chk(f.organic_matter_pct) + '</td>'
            + '<td style="text-align:center;">' + chk(f.soil_ph) + '</td>'
            + '<td style="text-align:center;">' + chk(f.cec_meq100g) + '</td>'
            + '<td style="text-align:center;">' + chk(f.available_water_capacity_in) + '</td>'
            + '<td style="text-align:center;">' + chk(f.drainage_class) + '</td>'
            + '<td style="text-align:center;">' + chk(f.erosion_risk) + '</td>'
            + '<td style="text-align:center;">' + chk(f.dominant_mapunit_name) + '</td>'
            + '<td style="text-align:center;">' + chk(f.ssurgo_coverage_pct) + '</td></tr>';
    }}
    provHtml += '</tbody></table>';
    document.getElementById('provenanceTable').innerHTML = provHtml;
}})();

// --- Populate filters immediately ---
(function() {{
    var cropSel = document.getElementById('cropFilter');
    if (cropSel) {{
        var crops = new Set();
        fieldsData.forEach(function(f) {{ if (f.crop) crops.add(f.crop); }});
        var cropOrder = ['Corn', 'Soybeans', 'Forest', 'Grass/Pasture'];
        Array.from(crops).sort(function(a, b) {{
            var ai = cropOrder.indexOf(a);
            var bi = cropOrder.indexOf(b);
            ai = ai === -1 ? 100 + a.localeCompare(b) : ai;
            bi = bi === -1 ? 100 + b.localeCompare(a) : bi;
            return ai - bi;
        }}).forEach(function(c) {{
            var opt = document.createElement('option');
            opt.value = c; opt.textContent = c;
            cropSel.appendChild(opt);
        }});
    }}
    var riskSel = document.getElementById('riskFilter');
    if (riskSel) {{
        var risks = new Set();
        fieldsData.forEach(function(f) {{ if (f.risk_category) risks.add(f.risk_category); }});
        var riskOrder = ['Low', 'Moderate', 'Elevated', 'High'];
        Array.from(risks).sort(function(a, b) {{ return riskOrder.indexOf(a) - riskOrder.indexOf(b); }}).forEach(function(r) {{
            var opt = document.createElement('option');
            opt.value = r; opt.textContent = r;
            riskSel.appendChild(opt);
        }});
    }}
    renderFieldCheckboxMenu();
    document.addEventListener('click', function(evt) {{
        var wrap = document.getElementById('fieldFilterWrap');
        if (wrap && !wrap.contains(evt.target)) closeFieldMenu();
    }});
}})();

window.addEventListener('resize', function() {{
    ['rankingChart','refRankingChart','mapChart','ndviChart','gddPrecipChart','weatherChart','soilChart'].forEach(function(id) {{
        var el = document.getElementById(id);
        if (el && el.data && el.layout) Plotly.Plots.resize(el);
    }});
}});
</script>
</body>
</html>"""

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        f.write(html)
    log.info(f"Dashboard written to {out_path}")
    return out_path
