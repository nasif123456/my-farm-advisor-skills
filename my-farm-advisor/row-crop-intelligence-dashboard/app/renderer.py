#!/usr/bin/env python3
"""HTML dashboard rendering with Plotly.js for the row crop intelligence dashboard."""

from __future__ import annotations

import base64
import json
import logging
import math
import tempfile
from io import BytesIO
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import numpy as np
from PIL import Image

from .data_loader import DashboardData

log = logging.getLogger(__name__)

COLORBLIND_PALETTE = [
    "#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd",
    "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf",
]

RISK_COLORS = {"Low": "#2ca02c", "Moderate": "#ff7f0e", "Elevated": "#d62728", "High": "#8b0000"}
PRIORITY_COLORS = {"Routine": "#2ca02c", "Monitor": "#ff7f0e", "Priority": "#d62728"}


def _color_for_field(idx: int) -> str:
    return COLORBLIND_PALETTE[idx % len(COLORBLIND_PALETTE)]


def _wgs84_to_mercator(lon: float, lat: float) -> tuple[float, float]:
    earth_radius = 6378137.0
    x = earth_radius * math.radians(lon)
    y = earth_radius * math.log(math.tan(math.pi / 4 + math.radians(lat) / 2))
    return x, y


def _field_to_mercator_polygons(gdf: gpd.GeoDataFrame) -> dict[str, list[list[list[float]]]]:
    result = {}
    for _, row in gdf.iterrows():
        fid = row["field_id"]
        polygons = []
        geom = row.geometry
        if geom.geom_type == "Polygon":
            coords = [_wgs84_to_mercator(x, y) for x, y in geom.exterior.coords]
            polygons.append([[round(c[0], 2), round(c[1], 2)] for c in coords])
        elif geom.geom_type == "MultiPolygon":
            for poly in geom.geoms:
                coords = [_wgs84_to_mercator(x, y) for x, y in poly.exterior.coords]
                polygons.append([[round(c[0], 2), round(c[1], 2)] for c in coords])
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
    mercator_polys = _field_to_mercator_polygons(gdf) if gdf is not None and not gdf.empty else {}
    fields = []
    for _, row in df.iterrows():
        fid = row["field_id"]
        f = {
            "field_id": fid,
            "area_acres": row.get("area_acres", 0),
            "crop": row.get("crop_name", ""),
            "ndvi_score": _safe_float(row.get("ndvi_score")),
            "stability_score": _safe_float(row.get("stability_score")),
            "soil_health_score": _safe_float(row.get("soil_health_score")),
            "weather_suitability": _safe_float(row.get("weather_suitability_score")),
            "fis": _safe_float(row.get("field_intelligence_score")),
            "crop_stress": _safe_float(row.get("crop_stress_indicator")),
            "conservation_priority": _safe_float(row.get("conservation_priority_score")),
            "risk_category": row.get("risk_category", ""),
            "priority_category": row.get("priority_category", ""),
            "total_precipitation_mm": _safe_float(row.get("total_precipitation_mm")),
            "cumulative_gdd": _safe_float(row.get("cumulative_gdd")),
            "organic_matter_pct": _safe_float(row.get("organic_matter_pct")),
            "soil_ph": _safe_float(row.get("soil_ph")),
            "drainage_class": row.get("drainage_class", ""),
            "mercator_polygons": mercator_polys.get(fid, []),
        }
        for peak in ["ndvi_corn_peak_95", "ndvi_soybean_peak_95"]:
            if peak in row:
                f[peak] = _safe_float(row[peak])
        fields.append(f)
    return fields


def _safe_float(v: Any) -> float | None:
    if v is None or (isinstance(v, float) and math.isnan(v)) or (isinstance(v, str) and v == ""):
        return None
    try:
        return round(float(v), 2)
    except (ValueError, TypeError):
        return None


def _make_ranking_chart_json(fields_data: list[dict]) -> str:
    fields_data = sorted(fields_data, key=lambda f: f.get("fis") or 0, reverse=True)
    labels = [f["field_id"] for f in fields_data]
    values = [f.get("fis") or 0 for f in fields_data]
    colors = [RISK_COLORS.get(f.get("risk_category", ""), "#7f7f7f") for f in fields_data]
    trace = {
        "type": "bar",
        "x": values,
        "y": labels,
        "orientation": "h",
        "marker": {"color": colors},
        "text": [f"FIS: {v:.0f}" if v else "N/A" for v in values],
        "textposition": "outside",
        "hovertemplate": (
            "<b>%{y}</b><br>"
            "Field Intelligence Score: %{x:.1f}<br>"
            "Risk: %{customdata}<extra></extra>"
        ),
        "customdata": [f.get("risk_category", "") for f in fields_data],
    }
    layout = {
        "title": {"text": "Field Intelligence Score Ranking"},
        "xaxis": {"title": "Field Intelligence Score (0-100)", "range": [0, 105]},
        "yaxis": {"title": "", "automargin": True},
        "height": max(300, len(labels) * 40),
        "margin": {"l": 180, "r": 40, "t": 50, "b": 40},
        "bargap": 0.3,
    }
    return json.dumps([trace], cls=_NumpyEncoder), json.dumps(layout, cls=_NumpyEncoder)


def _make_ndvi_chart_json(fields_data: list[dict]) -> tuple[str, str]:
    traces = []
    for i, f in enumerate(fields_data):
        ndvi_vals = []
        labels = []
        for key, label in [("ndvi_corn", "Corn"), ("ndvi_soybean", "Soybean")]:
            val = f.get(key)
            if val is not None:
                ndvi_vals.append(val)
                labels.append(label)
        if ndvi_vals:
            traces.append({
                "type": "bar",
                "name": f["field_id"],
                "x": labels,
                "y": ndvi_vals,
                "marker": {"color": _color_for_field(i)},
                "hovertemplate": f["field_id"] + "<br>%{x}: %{y:.3f}<extra></extra>",
            })
    layout = {
        "title": {"text": "Crop Mean NDVI by Field"},
        "yaxis": {"title": "Mean NDVI", "range": [0, 1]},
        "barmode": "group",
        "height": 350,
        "margin": {"t": 40, "b": 80, "l": 60, "r": 20},
    }
    return json.dumps(traces, cls=_NumpyEncoder), json.dumps(layout, cls=_NumpyEncoder)


def _make_soil_chart_json(fields_data: list[dict]) -> tuple[str, str]:
    labels = [f["field_id"] for f in fields_data]
    soil_scores = [f.get("soil_health_score") or 0 for f in fields_data]
    colors = ["#2ca02c" if s >= 60 else "#ff7f0e" if s >= 40 else "#d62728" for s in soil_scores]
    trace = {
        "type": "bar",
        "x": labels,
        "y": soil_scores,
        "marker": {"color": colors},
        "hovertemplate": "<b>%{x}</b><br>Soil Health Score: %{y:.1f}<extra></extra>",
    }
    layout = {
        "title": {"text": "Soil Health Score by Field"},
        "yaxis": {"title": "Score (0-100)", "range": [0, 105]},
        "height": 350,
        "margin": {"t": 40, "b": 100, "l": 60, "r": 20},
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
            precip = fd.groupby(fd["date"].dt.month)["PRECTOTCORR"].sum()
            traces.append({
                "type": "bar",
                "name": f"{fid} Precip",
                "x": [f"{int(m):02d}" for m in precip.index],
                "y": precip.round(1).tolist(),
                "yaxis": "y2",
                "marker": {"color": _color_for_field(i), "opacity": 0.3},
                "hovertemplate": f"{fid}<br>Month: %{{x}}<br>Rainfall: %{{y:.0f}} mm<extra></extra>",
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
        "hovertemplate": "<b>%{x}</b><br>Crop Stress: %{y:.1f}/100<br>%{text}<extra></extra>",
        "text": ["Low" if s <= 30 else "Moderate" if s <= 60 else "High" for s in stress],
    }
    layout = {
        "title": {"text": "Crop Stress Indicator"},
        "yaxis": {"title": "Stress (0-100)", "range": [0, 105]},
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


def _make_map_json(fields_data: list[dict]) -> tuple[str, str]:
    map_traces = []
    for i, f in enumerate(fields_data):
        for poly in f.get("mercator_polygons", []):
            xs = [p[0] for p in poly] + [poly[0][0]]
            ys = [p[1] for p in poly] + [poly[0][1]]
            risk = f.get("risk_category", "")
            color = RISK_COLORS.get(risk, "#7f7f7f")
            map_traces.append({
                "type": "scatter",
                "mode": "lines",
                "fill": "toself",
                "name": f["field_id"],
                "x": xs,
                "y": ys,
                "line": {"color": color, "width": 2},
                "fillcolor": color + "60",
                "legendgroup": f["field_id"],
                "showlegend": i == 0,
                "hovertemplate": (
                    f"<b>{f['field_id']}</b><br>"
                    f"FIS: {f.get('fis', 'N/A'):.0f}<br>"
                    f"Crop: {f.get('crop', 'N/A')}<br>"
                    f"Acres: {f.get('area_acres', 0):.0f}<br>"
                    f"NDVI Score: {f.get('ndvi_score', 'N/A')}<br>"
                    f"Soil Score: {f.get('soil_health_score', 'N/A')}<br>"
                    f"Risk: {risk}<extra></extra>"
                ),
            })
    layout = {
        "title": {"text": "Field Boundaries - Intelligence Score (color = risk category)"},
        "showlegend": False,
        "height": 500,
        "margin": {"t": 40, "b": 20, "l": 20, "r": 20},
        "dragmode": "zoom",
        "map": {"style": "open-street-map"},
    }
    return json.dumps(map_traces, cls=_NumpyEncoder), json.dumps(layout, cls=_NumpyEncoder)


def _make_kpi_html(fields_data: list[dict]) -> str:
    n_fields = len(fields_data)
    total_acres = sum(f.get("area_acres") or 0 for f in fields_data)
    fis_vals = [f.get("fis") for f in fields_data if f.get("fis") is not None]
    mean_fis = sum(fis_vals) / len(fis_vals) if fis_vals else 0
    precip_vals = [f.get("total_precipitation_mm") for f in fields_data if f.get("total_precipitation_mm") is not None]
    mean_precip = sum(precip_vals) / len(precip_vals) if precip_vals else 0
    ndvi_vals = [f.get("ndvi_score") for f in fields_data if f.get("ndvi_score") is not None]
    mean_ndvi = sum(ndvi_vals) / len(ndvi_vals) if ndvi_vals else 0
    gdd_vals = [f.get("cumulative_gdd") for f in fields_data if f.get("cumulative_gdd") is not None]
    mean_gdd = sum(gdd_vals) / len(gdd_vals) if gdd_vals else 0
    n_high_priority = sum(1 for f in fields_data if f.get("priority_category") == "Priority")
    n_low = sum(1 for f in fields_data if f.get("risk_category") == "Low")

    cards = [
        ("Fields", str(n_fields), "#1f77b4"),
        ("Total Acres", f"{total_acres:,.0f}", "#2ca02c"),
        ("Mean FIS", f"{mean_fis:.0f}/100", "#9467bd"),
        ("Mean NDVI", f"{mean_ndvi:.0f}/100", "#1f77b4"),
        ("Mean Rainfall", f"{mean_precip:.0f} mm", "#17becf"),
        ("Mean GDD", f"{mean_gdd:.0f}", "#ff7f0e"),
    ]
    html = '<div style="display:flex;flex-wrap:wrap;gap:12px;margin:16px 0;">'
    for label, value, color in cards:
        html += f"""
        <div style="flex:1;min-width:120px;padding:16px;background:{color};border-radius:8px;text-align:center;">
            <div style="font-size:28px;font-weight:700;color:#fff;">{value}</div>
            <div style="font-size:13px;color:rgba(255,255,255,0.9);margin-top:4px;">{label}</div>
        </div>"""
    html += '</div>'
    return html


def _make_field_table_html(fields_data: list[dict]) -> str:
    rows_html = ""
    for f in sorted(fields_data, key=lambda x: x.get("fis") or 0, reverse=True):
        risk = f.get("risk_category", "")
        priority = f.get("priority_category", "")
        risk_color = RISK_COLORS.get(risk, "#7f7f7f")
        pri_color = PRIORITY_COLORS.get(priority, "#7f7f7f")
        fis = f.get("fis")
        rows_html += f"""
        <tr>
            <td>{f['field_id']}</td>
            <td>{f.get('crop', 'N/A')}</td>
            <td>{f.get('area_acres', 0):.0f}</td>
            <td style="text-align:center;">{f.get('ndvi_score', 'N/A')}</td>
            <td style="text-align:center;">{f.get('soil_health_score', 'N/A')}</td>
            <td style="text-align:center;">{f.get('crop_stress', 'N/A')}</td>
            <td style="text-align:center;"><b>{fis:.0f}</b> {fis and (fis >= 80 and "✅" or fis >= 65 and "⚠️" or fis >= 50 and "🔶" or "🔴") or ""}</td>
            <td style="text-align:center;"><span style="background:{risk_color};color:#fff;padding:2px 8px;border-radius:10px;font-size:12px;">{risk}</span></td>
            <td style="text-align:center;"><span style="background:{pri_color};color:#fff;padding:2px 8px;border-radius:10px;font-size:12px;">{priority}</span></td>
        </tr>"""
    return f"""
    <div style="overflow-x:auto;">
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead style="background:#f0f0f0;">
            <tr>
                <th style="padding:8px;text-align:left;">Field</th>
                <th style="padding:8px;text-align:left;">Crop</th>
                <th style="padding:8px;text-align:right;">Acres</th>
                <th style="padding:8px;text-align:center;">NDVI</th>
                <th style="padding:8px;text-align:center;">Soil</th>
                <th style="padding:8px;text-align:center;">Stress</th>
                <th style="padding:8px;text-align:center;">FIS</th>
                <th style="padding:8px;text-align:center;">Risk</th>
                <th style="padding:8px;text-align:center;">Priority</th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
    </div>"""


def _make_soil_table_html(fields_data: list[dict]) -> str:
    rows_html = ""
    for f in sorted(fields_data, key=lambda x: x.get("soil_health_score") or 0, reverse=True):
        rows_html += f"""
        <tr>
            <td>{f['field_id']}</td>
            <td style="text-align:center;">{f.get('soil_health_score', 'N/A')}</td>
            <td style="text-align:center;">{f.get('organic_matter_pct', 'N/A')}</td>
            <td style="text-align:center;">{f.get('soil_ph', 'N/A')}</td>
            <td style="text-align:center;">{f.get('drainage_class', 'N/A')}</td>
            <td style="text-align:center;">{f.get('conservation_priority', 'N/A')}</td>
        </tr>"""
    return f"""
    <div style="overflow-x:auto;">
    <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead style="background:#f0f0f0;">
            <tr>
                <th style="padding:8px;text-align:left;">Field</th>
                <th style="padding:8px;text-align:center;">Soil Score</th>
                <th style="padding:8px;text-align:center;">OM (%)</th>
                <th style="padding:8px;text-align:center;">pH</th>
                <th style="padding:8px;text-align:center;">Drainage</th>
                <th style="padding:8px;text-align:center;">Conservation</th>
            </tr>
        </thead>
        <tbody>
            {rows_html}
        </tbody>
    </table>
    </div>"""


def render_dashboard(d: DashboardData, output_path: str | Path, title: str = "Row Crop Intelligence Dashboard"):
    fields_data = _build_field_data(d.field_summary, d.field_boundaries)
    interpretations = d.interpretations
    grower_summary = interpretations.get("grower_summary", "") if interpretations else ""
    key_findings = interpretations.get("key_findings", []) if interpretations else []
    per_field_interp = interpretations.get("per_field", {}) if interpretations else {}

    fis_traces, fis_layout = _make_ranking_chart_json(fields_data)
    ndvi_traces, ndvi_layout = _make_ndvi_chart_json(fields_data)
    soil_traces, soil_layout = _make_soil_chart_json(fields_data)
    weather_traces, weather_layout = _make_weather_radar_json(fields_data)
    stress_traces, stress_layout = _make_stress_chart_json(fields_data)
    gdd_precip_traces, gdd_precip_layout = _make_gdd_precip_chart_json(fields_data, d.weather_timeseries)
    map_traces, map_layout = _make_map_json(fields_data)

    kpi_html = _make_kpi_html(fields_data)
    table_html = _make_field_table_html(fields_data)
    soil_table_html = _make_soil_table_html(fields_data)

    findings_html = ""
    if key_findings:
        findings_html = '<div style="margin:16px 0;"><h3 style="margin-bottom:8px;">Key Findings</h3><ul>'
        for finding in key_findings:
            findings_html += f"<li style='margin-bottom:6px;'>{finding}</li>"
        findings_html += '</ul></div>'

    summary_html = f'<div style="margin:16px 0;padding:16px;background:#f8f9fa;border-radius:8px;border-left:4px solid #1f77b4;"><p style="margin:0;font-size:14px;line-height:1.6;">{grower_summary}</p></div>' if grower_summary else ""

    plotly_cdn = "https://cdn.plot.ly/plotly-2.35.2.min.js"

    field_dropdown_options = ''.join(f'<option value="{f["field_id"]}">{f["field_id"]}</option>' for f in fields_data)

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<script src="{plotly_cdn}"></script>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #fff; color: #333; line-height: 1.5; }}
.container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}
.header {{ background: linear-gradient(135deg, #1b5e20, #2e7d32); color: #fff; padding: 24px 32px; border-radius: 12px; margin-bottom: 24px; }}
.header h1 {{ font-size: 24px; margin-bottom: 4px; }}
.header p {{ font-size: 14px; opacity: 0.9; }}
.section {{ background: #fff; border: 1px solid #e0e0e0; border-radius: 8px; padding: 20px; margin-bottom: 20px; }}
.section h2 {{ font-size: 18px; color: #1b5e20; margin-bottom: 16px; padding-bottom: 8px; border-bottom: 2px solid #e8f5e9; }}
.chart-container {{ width: 100%; margin: 8px 0; }}
.chart-row {{ display: flex; gap: 16px; flex-wrap: wrap; }}
.chart-row > div {{ flex: 1; min-width: 320px; }}
.filters {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: center; margin-bottom: 16px; }}
.filters label {{ font-size: 13px; font-weight: 600; }}
.filters select {{ padding: 6px 10px; border: 1px solid #ccc; border-radius: 4px; font-size: 13px; }}
.interpretation {{ background: #f8f9fa; border-radius: 8px; padding: 16px; margin-top: 12px; font-size: 14px; line-height: 1.6; }}
.interpretation h4 {{ color: #1f77b4; margin-bottom: 6px; font-size: 14px; }}
@media (max-width: 768px) {{ .chart-row > div {{ min-width: 100%; }} }}
.finding-good {{ color: #2e7d32; }}
.finding-warn {{ color: #e65100; }}
.finding-critical {{ color: #c62828; }}
</style>
</head>
<body>
<div class="container">
    <div class="header">
        <h1>{title}</h1>
        <p>Integrated field intelligence combining crop health, weather, soil, and spatial analysis</p>
        <p style="font-size:12px;margin-top:4px;opacity:0.7;">Generated: {pd.Timestamp.now().strftime("%Y-%m-%d %H:%M")}</p>
    </div>

    <div class="section">
        <h2>Farm Overview</h2>
        {kpi_html}
        <div class="filters">
            <label for="fieldFilter">Field:</label>
            <select id="fieldFilter" onchange="applyFieldFilter()">
                <option value="">All Fields</option>
                {field_dropdown_options}
            </select>
        </div>
        {summary_html}
        {findings_html}
        <div class="chart-container"><div id="rankingChart"></div></div>
    </div>

    <div class="section">
        <h2>Spatial Field Map</h2>
        <p style="font-size:13px;color:#666;margin-bottom:12px;">Field boundaries colored by risk category. Hover for details. Zoom and pan with mouse.</p>
        <div class="chart-container"><div id="mapChart" style="height:500px;"></div></div>
    </div>

    <div class="section">
        <h2>Crop Health & Field Variability</h2>
        <div class="chart-row">
            <div>
                <div class="chart-container"><div id="ndviChart"></div></div>
            </div>
            <div>
                <div class="chart-container"><div id="stressChart"></div></div>
            </div>
        </div>
        <div style="margin-top:16px;">
            <h3 style="font-size:15px;margin-bottom:8px;">Field Comparison</h3>
            {table_html}
        </div>
    </div>

    <div class="section">
        <h2>Weather & Climate Intelligence</h2>
        <div class="chart-container"><div id="gddPrecipChart"></div></div>
        <div class="chart-row">
            <div>
                <div class="chart-container"><div id="weatherChart"></div></div>
            </div>
        </div>
    </div>

    <div class="section">
        <h2>Soil Health & Sustainability</h2>
        <div class="chart-row">
            <div>
                <div class="chart-container"><div id="soilChart"></div></div>
            </div>
        </div>
        <div style="margin-top:16px;">
            <h3 style="font-size:15px;margin-bottom:8px;">Soil Properties by Field</h3>
            {soil_table_html}
        </div>
    </div>

    <div class="section">
        <h2>Methodology & Limitations</h2>
        <div style="font-size:13px;line-height:1.7;color:#555;">
            <p><strong>Field Intelligence Score</strong> = 0.40 × Crop Health + 0.30 × Soil Condition + 0.20 × Weather Suitability + 0.10 × Stability.</p>
            <p><strong>Soil Health Score</strong> = weighted combination of organic matter, pH, available water capacity, drainage class, and erosion risk.</p>
            <p><strong>Crop Stress Indicator</strong> = based on mean NDVI, NDVI variability, and peak NDVI timing. Higher values indicate more apparent stress.</p>
            <p><strong>Conservation Priority</strong> = combined soil limitation, crop stress, erosion risk, and weather exposure.</p>
            <p><strong>Important limitations:</strong></p>
            <ul style="margin-left:20px;">
                <li>Scores are relative to the fields and data in this dashboard only.</li>
                <li>NDVI measures greenness, not yield or specific crop health conditions.</li>
                <li>Weather data is from NASA POWER at grid resolution (~0.5°), not from on-farm stations.</li>
                <li>Soil data is from SSURGO at 1:12,000 to 1:63,360 scale and may not reflect within-field variability.</li>
                <li>These results support prioritisation — they do not replace field scouting or professional judgement.</li>
                <li>Missing data may influence composite scores.</li>
            </ul>
        </div>
    </div>
</div>

<script>
var fieldsData = {json.dumps(fields_data, cls=_NumpyEncoder)};

function applyFieldFilter() {{
    var val = document.getElementById('fieldFilter').value;
    Plotly.update('rankingChart', {{}}, {{}});
    Plotly.update('mapChart', {{}}, {{}});
}}

var fisTrace = {fis_traces};
var fisLayout = {fis_layout};
Plotly.newPlot('rankingChart', fisTrace, fisLayout, {{responsive: true}});

var mapTrace = {map_traces};
var mapLayout = {map_layout};
Plotly.newPlot('mapChart', mapTrace, mapLayout, {{responsive: true}});

var ndviTrace = {ndvi_traces};
var ndviLayout = {ndvi_layout};
Plotly.newPlot('ndviChart', ndviTrace, ndviLayout, {{responsive: true}});

var stressTrace = {stress_traces};
var stressLayout = {stress_layout};
Plotly.newPlot('stressChart', stressTrace, stressLayout, {{responsive: true}});

var gddPrecipTrace = {gdd_precip_traces};
var gddPrecipLayout = {gdd_precip_layout};
Plotly.newPlot('gddPrecipChart', gddPrecipTrace, gddPrecipLayout, {{responsive: true}});

var weatherTrace = {weather_traces};
var weatherLayout = {weather_layout};
Plotly.newPlot('weatherChart', weatherTrace, weatherLayout, {{responsive: true}});

var soilTrace = {soil_traces};
var soilLayout = {soil_layout};
Plotly.newPlot('soilChart', soilTrace, soilLayout, {{responsive: true}});

window.addEventListener('resize', function() {{
    Plotly.Plots.resize(document.getElementById('rankingChart'));
    Plotly.Plots.resize(document.getElementById('mapChart'));
    Plotly.Plots.resize(document.getElementById('ndviChart'));
    Plotly.Plots.resize(document.getElementById('stressChart'));
    Plotly.Plots.resize(document.getElementById('gddPrecipChart'));
    Plotly.Plots.resize(document.getElementById('weatherChart'));
    Plotly.Plots.resize(document.getElementById('soilChart'));
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
