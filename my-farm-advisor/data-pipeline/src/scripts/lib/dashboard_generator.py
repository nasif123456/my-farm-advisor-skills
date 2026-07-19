#!/usr/bin/env python3
"""Core module for offline Grower Field Weather Dashboard generation.

Provides farm directory discovery, data loading, weather transformations,
Mercator projection, Esri tile acquisition, Plotly vendoring, and HTML assembly.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import geopandas as gpd
import pandas as pd
import requests
from PIL import Image

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

COLORBLIND_PALETTE = [
    "#1f77b4",
    "#ff7f0e",
    "#2ca02c",
    "#d62728",
    "#9467bd",
    "#8c564b",
    "#e377c2",
    "#7f7f7f",
    "#bcbd22",
    "#17becf",
]

PLOTLY_VERSION = "2.35.2"
PLOTLY_FILENAME = f"plotly-{PLOTLY_VERSION}.min.js"
PLOTLY_CDN_URL = f"https://cdn.plot.ly/plotly-{PLOTLY_VERSION}.min.js"

ESRI_TILE_URL = (
    "https://server.arcgisonline.com/ArcGIS/rest/services/"
    "World_Imagery/MapServer/tile/{z}/{y}/{x}"
)
ESRI_MAX_ZOOM = 19
TILE_SIZE = 256
TILE_TIMEOUT = 10
TILE_MAX_RETRIES = 3
TARGET_IMAGE_WIDTH = 1500

DEFAULT_X_RANGE = [80, 320]

# ---------------------------------------------------------------------------
# Color allocation
# ---------------------------------------------------------------------------


def allocate_colors(field_ids: list[str]) -> dict[str, str]:
    """Allocate deterministic colors using the colorblind-safe palette."""
    return {
        fid: COLORBLIND_PALETTE[i % len(COLORBLIND_PALETTE)]
        for i, fid in enumerate(field_ids)
    }


# ---------------------------------------------------------------------------
# Projection helpers (WGS84 <-> Web Mercator)
# ---------------------------------------------------------------------------

_EARTH_RADIUS = 6378137.0
_EARTH_CIRCUMFERENCE = 2 * math.pi * _EARTH_RADIUS
_HALF_CIRCUMFERENCE = _EARTH_CIRCUMFERENCE / 2.0


def _mercator_x(lon: float) -> float:
    return _EARTH_RADIUS * math.radians(lon)


def _mercator_y(lat: float) -> float:
    rad = math.radians(lat)
    return _EARTH_RADIUS * math.log(math.tan(math.pi / 4 + rad / 2))


def wgs84_to_mercator(lon: float, lat: float) -> tuple[float, float]:
    return _mercator_x(lon), _mercator_y(lat)


def wgs84_ring_to_mercator(ring: list[list[float]]) -> list[list[float]]:
    return [list(wgs84_to_mercator(lon, lat)) for lon, lat in ring]


def geometry_to_mercator_polygons(geom: dict) -> list[list[list[float]]]:
    rings: list[list[list[float]]] = []
    geom_type = geom.get("type", "")
    if geom_type == "Polygon":
        for ring_coords in geom.get("coordinates", []):
            rings.append(wgs84_ring_to_mercator(ring_coords))
    elif geom_type == "MultiPolygon":
        for poly_coords in geom.get("coordinates", []):
            for ring_coords in poly_coords:
                rings.append(wgs84_ring_to_mercator(ring_coords))
    return rings


def mercator_bounds_from_rings(
    rings: list[list[list[float]]], buffer: float = 0.15
) -> tuple[float, float, float, float]:
    all_x = [p[0] for ring in rings for p in ring]
    all_y = [p[1] for ring in rings for p in ring]
    if not all_x:
        return -_HALF_CIRCUMFERENCE, -_HALF_CIRCUMFERENCE, _HALF_CIRCUMFERENCE, _HALF_CIRCUMFERENCE
    xmin, xmax = min(all_x), max(all_x)
    ymin, ymax = min(all_y), max(all_y)
    dx = (xmax - xmin) * buffer
    dy = (ymax - ymin) * buffer
    return xmin - dx, ymin - dy, xmax + dx, ymax + dy


# ---------------------------------------------------------------------------
# Tile math
# ---------------------------------------------------------------------------


def _mercator_to_tile_xy(mx: float, my: float, zoom: int) -> tuple[int, int]:
    resolution = _EARTH_CIRCUMFERENCE / (TILE_SIZE * (2**zoom))
    px = (mx + _HALF_CIRCUMFERENCE) / resolution
    py = (_HALF_CIRCUMFERENCE - my) / resolution
    return int(math.floor(px / TILE_SIZE)), int(math.floor(py / TILE_SIZE))


def _choose_zoom(xmin: float, xmax: float) -> int:
    world_width = _EARTH_CIRCUMFERENCE
    for z in range(1, 20):
        tile_width_px = TILE_SIZE * (2**z)
        meter_per_px = world_width / tile_width_px
        image_width_px = (xmax - xmin) / meter_per_px
        if image_width_px >= TARGET_IMAGE_WIDTH:
            return min(z, ESRI_MAX_ZOOM)
    return ESRI_MAX_ZOOM


# ---------------------------------------------------------------------------
# Esri tile acquisition
# ---------------------------------------------------------------------------


def acquire_basemap(
    fields_gdf: gpd.GeoDataFrame,
    cache_dir: Path | None = None,
    no_basemap: bool = False,
) -> tuple[str | None, bool]:
    if no_basemap:
        return None, False

    wgs84_rings: list[list[list[float]]] = []
    for geom in fields_gdf.geometry:
        gj = _geo_interface(geom)
        wgs84_rings.extend(geometry_to_mercator_polygons(gj))
    if not wgs84_rings:
        return None, False

    xmin, ymin, xmax, ymax = mercator_bounds_from_rings(wgs84_rings, buffer=0.15)
    zoom = _choose_zoom(xmin, xmax)

    tx_min, ty_min = _mercator_to_tile_xy(xmin, ymax, zoom)
    tx_max, ty_max = _mercator_to_tile_xy(xmax, ymin, zoom)
    tx_min = max(0, tx_min)
    ty_min = max(0, ty_min)
    max_tile = (2**zoom) - 1
    tx_max = min(tx_max, max_tile)
    ty_max = min(ty_max, max_tile)

    if tx_max < tx_min or ty_max < ty_min:
        return None, False

    extent_key = f"{xmin:.2f}_{ymin:.2f}_{xmax:.2f}_{ymax:.2f}_{zoom}"
    extent_hash = hashlib.md5(extent_key.encode()).hexdigest()[:12]

    if cache_dir is not None:
        cache_path = cache_dir / f"basemap_{extent_hash}.png"
        if cache_path.exists():
            b64 = base64.b64encode(cache_path.read_bytes()).decode("utf-8")
            return b64, True

    nx = tx_max - tx_min + 1
    ny = ty_max - ty_min + 1

    stitched = Image.new("RGB", (nx * TILE_SIZE, ny * TILE_SIZE))
    any_tile_ok = False

    for dx in range(nx):
        for dy in range(ny):
            tile_x = tx_min + dx
            tile_y = ty_min + dy
            tile_data: bytes | None = None
            for attempt in range(TILE_MAX_RETRIES):
                try:
                    url = ESRI_TILE_URL.format(z=zoom, y=tile_y, x=tile_x)
                    resp = requests.get(url, timeout=TILE_TIMEOUT)
                    resp.raise_for_status()
                    tile_data = resp.content
                    break
                except Exception:
                    if attempt < TILE_MAX_RETRIES - 1:
                        time.sleep(1)
            if tile_data is None:
                log.warning("Tile %d/%d z%d unavailable", tile_x, tile_y, zoom)
                continue
            any_tile_ok = True
            tile_img = Image.open(BytesIO(tile_data))
            stitched.paste(tile_img, (dx * TILE_SIZE, dy * TILE_SIZE))

    if not any_tile_ok:
        return None, False

    resolution = _EARTH_CIRCUMFERENCE / (TILE_SIZE * (2**zoom))
    origin_x = -_HALF_CIRCUMFERENCE + tx_min * TILE_SIZE * resolution
    origin_y = _HALF_CIRCUMFERENCE - ty_min * TILE_SIZE * resolution

    px_offset_x = int((xmin - origin_x) / resolution)
    px_offset_y = int((origin_y - ymax) / resolution)
    px_w = int((xmax - xmin) / resolution)
    px_h = int((ymax - ymin) / resolution)

    cropped = stitched.crop(
        (px_offset_x, px_offset_y, px_offset_x + px_w, px_offset_y + px_h)
    )

    buf = BytesIO()
    cropped.save(buf, format="PNG", optimize=True)
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(buf.getvalue())

    return b64, True


def _geo_interface(geom) -> dict:
    if hasattr(geom, "__geo_interface__"):
        return geom.__geo_interface__
    return {"type": "Polygon", "coordinates": [list(geom.exterior.coords)]}


# ---------------------------------------------------------------------------
# Plotly vendoring
# ---------------------------------------------------------------------------


def vendor_plotly(shared_root: Path) -> str:
    vendor_dir = shared_root / "vendor"
    vendor_dir.mkdir(parents=True, exist_ok=True)
    plotly_path = vendor_dir / PLOTLY_FILENAME

    if plotly_path.exists():
        return plotly_path.read_text(encoding="utf-8")

    log.info("Downloading Plotly.js %s from CDN ...", PLOTLY_VERSION)
    try:
        resp = requests.get(PLOTLY_CDN_URL, timeout=60)
        resp.raise_for_status()
        plotly_path.write_text(resp.text, encoding="utf-8")
        return resp.text
    except Exception as exc:
        raise RuntimeError(
            f"Failed to download Plotly.js from {PLOTLY_CDN_URL}: {exc}"
        ) from exc


# ---------------------------------------------------------------------------
# Farm directory discovery
# ---------------------------------------------------------------------------


def discover_farm_dir(
    farm_dir: str | Path | None = None,
    growers_dir: str | Path | None = None,
    data_root: str | Path | None = None,
) -> Path:
    if farm_dir is not None:
        return _validate_farm_dir(Path(farm_dir).expanduser().resolve())

    if growers_dir is not None:
        return _find_unique_farm(Path(growers_dir).expanduser().resolve())

    if data_root is not None:
        root = Path(data_root).expanduser().resolve()
        growers_root = root / "growers"
        if growers_root.is_dir():
            try:
                return _find_unique_farm(growers_root)
            except ValueError:
                pass

    env_root = os.environ.get("DATA_PIPELINE_DATA_ROOT")
    if env_root:
        root = Path(env_root).expanduser().resolve()
        growers_root = root / "growers"
        if growers_root.is_dir():
            try:
                return _find_unique_farm(growers_root)
            except ValueError:
                pass

    home = Path.home()
    candidates: list[Path] = []
    for candidate in home.iterdir():
        if not candidate.is_dir():
            continue
        growers_candidate = candidate / "growers"
        if growers_candidate.is_dir():
            try:
                return _find_unique_farm(growers_candidate)
            except ValueError:
                for entry in growers_candidate.iterdir():
                    if entry.is_dir():
                        farms_dir = entry / "farms"
                        if farms_dir.is_dir():
                            for farm_entry in farms_dir.iterdir():
                                if farm_entry.is_dir() and _is_farm_dir(farm_entry):
                                    candidates.append(farm_entry)
        elif _is_farm_dir(candidate):
            candidates.append(candidate)

    if not candidates:
        raise ValueError(
            "No farm directory found. Provide --farm-dir, --growers-dir, "
            "set DATA_PIPELINE_DATA_ROOT, or ensure a valid runtime tree exists."
        )
    if len(candidates) > 1:
        paths = "\n  ".join(str(p) for p in candidates)
        raise ValueError(
            f"Multiple candidate farm directories found ({len(candidates)}):\n"
            f"  {paths}\n"
            "Use --farm-dir to select one."
        )
    return _validate_farm_dir(candidates[0])


def _find_unique_farm(growers_root: Path) -> Path:
    farms: list[Path] = []
    for grower_entry in sorted(growers_root.iterdir()):
        if not grower_entry.is_dir():
            continue
        farms_dir = grower_entry / "farms"
        if not farms_dir.is_dir():
            continue
        for farm_entry in sorted(farms_dir.iterdir()):
            if farm_entry.is_dir() and _is_farm_dir(farm_entry):
                farms.append(farm_entry)

    if not farms:
        raise ValueError(f"No valid farm directories found under {growers_root}")
    if len(farms) > 1:
        paths = "\n  ".join(str(p) for p in farms)
        raise ValueError(
            f"Multiple farm directories found under {growers_root}:\n"
            f"  {paths}\n"
            "Use --farm-dir to select one."
        )
    return _validate_farm_dir(farms[0])


def _is_farm_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    has_boundary = (path / "boundary" / "field_boundaries.geojson").is_file()
    has_fields = (path / "fields").is_dir()
    return has_boundary and has_fields


def _validate_farm_dir(path: Path) -> Path:
    path = path.expanduser().resolve()
    if not path.is_dir():
        raise ValueError(f"Farm directory does not exist: {path}")
    if not (path / "boundary" / "field_boundaries.geojson").is_file():
        raise ValueError(
            f"Farm directory missing required file: "
            f"{path / 'boundary' / 'field_boundaries.geojson'}"
        )
    if not (path / "fields").is_dir():
        raise ValueError(
            f"Farm directory missing required directory: {path / 'fields'}"
        )
    return path


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_farm_boundaries(farm_dir: Path) -> gpd.GeoDataFrame:
    path = farm_dir / "boundary" / "field_boundaries.geojson"
    if not path.exists():
        raise FileNotFoundError(f"Boundary file not found: {path}")
    gdf = gpd.read_file(path)
    if gdf.empty:
        raise ValueError(f"Boundary file contains no features: {path}")
    if "field_id" not in gdf.columns:
        raise ValueError(f"Boundary file missing 'field_id' property column: {path}")
    return gdf


def load_field_metadata(farm_dir: Path, field_id: str) -> dict[str, Any]:
    field_path = farm_dir / "fields" / field_id / "field.json"
    if not field_path.exists():
        return {"field_id": field_id}
    try:
        data = json.loads(field_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {"field_id": field_id}
    except (json.JSONDecodeError, OSError):
        log.warning("Failed to parse %s", field_path)
        return {"field_id": field_id}


def load_field_weather(farm_dir: Path, field_id: str) -> pd.DataFrame:
    path = farm_dir / "fields" / field_id / "weather" / "daily_weather.csv"
    if not path.exists():
        return pd.DataFrame()
    try:
        df = pd.read_csv(path)
        if df.empty or len(df.columns) == 0:
            return pd.DataFrame()
        return df
    except (pd.errors.EmptyDataError, pd.errors.ParserError, OSError):
        log.warning("Unable to parse weather CSV: %s", path)
        return pd.DataFrame()


def load_farm_aggregate_weather(farm_dir: Path) -> pd.DataFrame:
    tables_dir = farm_dir / "derived" / "tables"
    if not tables_dir.is_dir():
        return pd.DataFrame()
    for fpath in sorted(tables_dir.glob("*_weather_*.csv")):
        try:
            df = pd.read_csv(fpath)
            if not df.empty:
                return df
        except (pd.errors.EmptyDataError, pd.errors.ParserError, OSError):
            continue
    return pd.DataFrame()


# ---------------------------------------------------------------------------
# Weather transformations
# ---------------------------------------------------------------------------


def compute_field_year_weather(
    df: pd.DataFrame,
    field_id: str,
    year: int,
) -> list[dict[str, Any]]:
    required_cols = {"date", "T2M_MIN", "T2M_MAX", "PRECTOTCORR"}
    if not required_cols.issubset(df.columns):
        return []

    work = df.copy()
    work["date"] = pd.to_datetime(work["date"], errors="coerce")
    work = work.dropna(subset=["date"])
    work = work[work["date"].dt.year == year]
    if work.empty:
        return []

    work = work.sort_values("date").reset_index(drop=True)

    for col in ("T2M_MIN", "T2M_MAX", "PRECTOTCORR"):
        work[col] = pd.to_numeric(work[col], errors="coerce")

    work = work.dropna(subset=["T2M_MIN", "T2M_MAX", "PRECTOTCORR"])
    if work.empty:
        return []

    spring = work[work["date"].dt.month < 7]
    frost_rows = spring[spring["T2M_MIN"] <= 0.0]
    if frost_rows.empty:
        last_frost = pd.Timestamp(year=year, month=1, day=1)
    else:
        last_frost = frost_rows["date"].max()

    last_frost_doy = last_frost.dayofyear

    post_frost = work[work["date"] >= last_frost].copy()
    if post_frost.empty:
        return []

    post_frost["dayOfYear"] = post_frost["date"].dt.dayofyear
    post_frost["dailyGdd"] = (
        (post_frost["T2M_MAX"] + post_frost["T2M_MIN"]) / 2.0 - 10.0
    ).clip(lower=0.0)
    post_frost["dailyRainfallIn"] = post_frost["PRECTOTCORR"] * 0.0393701

    cum_gdd = 0.0
    cum_rain = 0.0
    records: list[dict[str, Any]] = []
    for _, row in post_frost.iterrows():
        cum_gdd += float(row["dailyGdd"])
        cum_rain += float(row["dailyRainfallIn"])
        records.append(
            {
                "date": row["date"].strftime("%Y-%m-%d"),
                "dayOfYear": int(row["dayOfYear"]),
                "dailyGdd": round(float(row["dailyGdd"]), 4),
                "cumulativeGdd": round(cum_gdd, 4),
                "dailyRainfallIn": round(float(row["dailyRainfallIn"]), 4),
                "cumulativeRainfallIn": round(cum_rain, 4),
            }
        )

    return records


# ---------------------------------------------------------------------------
# Dashboard data model builder
# ---------------------------------------------------------------------------


def build_dashboard_data(
    farm_dir: Path,
    fields_gdf: gpd.GeoDataFrame,
    no_basemap: bool = False,
    shared_root: Path | None = None,
) -> dict[str, Any]:
    farm_dir_resolved = farm_dir.resolve()
    farm_id = farm_dir_resolved.name

    grower_id = "unknown-grower"
    if len(farm_dir_resolved.parents) >= 3:
        p2 = farm_dir_resolved.parents[2]
        if p2.name == "farms":
            p3 = farm_dir_resolved.parents[3]
            grower_id = p3.name
        elif farm_dir_resolved.parents[1].name == "farms":
            grower_id = farm_dir_resolved.parents[2].name

    field_ids = sorted(fields_gdf["field_id"].astype(str).unique().tolist())
    colors = allocate_colors(field_ids)

    fields_data: list[dict[str, Any]] = []
    for fid in field_ids:
        feat_rows = fields_gdf[fields_gdf["field_id"].astype(str) == fid]
        mercator_rings: list[list[list[float]]] = []
        for _, row in feat_rows.iterrows():
            gj = _geo_interface(row.geometry)
            rings = geometry_to_mercator_polygons(gj)
            mercator_rings.extend(rings)

        meta = load_field_metadata(farm_dir, fid)
        field_name = meta.get("display_name", meta.get("field_id", fid))

        acres: float | None = None
        for _, row in feat_rows.iterrows():
            val = row.get("area_acres")
            if val is not None and pd.notna(val):
                acres = float(val)
                break

        fields_data.append(
            {
                "fieldId": fid,
                "fieldName": field_name,
                "acres": acres,
                "color": colors[fid],
                "mercatorPolygons": mercator_rings,
            }
        )

    weather_by_field_year: list[dict[str, Any]] = []
    fields_with_weather: set[str] = set()

    for fid in field_ids:
        fw_df = load_field_weather(farm_dir, fid)
        if fw_df.empty:
            continue

        fw_df["_date_parsed"] = pd.to_datetime(fw_df["date"], errors="coerce")
        years = sorted(
            int(y) for y in fw_df["_date_parsed"].dt.year.dropna().unique() if not pd.isna(y)
        )
        for year in years:
            records = compute_field_year_weather(fw_df, fid, year)
            if not records:
                continue
            last_frost = records[0]["date"]
            last_frost_doy = records[0]["dayOfYear"]
            weather_by_field_year.append(
                {
                    "fieldId": fid,
                    "year": year,
                    "lastFrostDate": last_frost,
                    "lastFrostDoy": last_frost_doy,
                    "daily": records,
                }
            )
            fields_with_weather.add(fid)

    for f in fields_data:
        fid = f["fieldId"]
        f["hasWeatherData"] = fid in fields_with_weather
        f["availableYears"] = sorted(
            list(
                set(
                    w["year"] for w in weather_by_field_year if w["fieldId"] == fid
                )
            )
        )

    if not weather_by_field_year:
        agg_df = load_farm_aggregate_weather(farm_dir)
        if not agg_df.empty:
            log.warning(
                "No per-field weather data found; farm aggregate CSV present "
                "but cannot reliably associate data with individual fields. "
                "Rendering dashboard without weather data."
            )

    basemap_b64, basemap_ok = None, False
    if shared_root is not None:
        cache_dir = shared_root / "vendor" / "tile_cache"
    else:
        cache_dir = None
    basemap_b64, basemap_ok = acquire_basemap(fields_gdf, cache_dir, no_basemap)

    return {
        "farm": {
            "farmId": farm_id,
            "farmName": farm_id.replace("-", " ").title(),
            "growerId": grower_id,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "basemapAvailable": basemap_ok,
            "basemapB64": basemap_b64,
        },
        "fields": fields_data,
        "weatherByFieldYear": weather_by_field_year,
    }


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------


def render_dashboard_html(
    data: dict[str, Any],
    plotly_js: str,
) -> str:
    fields_json = json.dumps(data["fields"], ensure_ascii=False)
    weather_json = json.dumps(data["weatherByFieldYear"], ensure_ascii=False)
    farm = data["farm"]

    basemap_img = ""
    if farm["basemapAvailable"] and farm.get("basemapB64"):
        basemap_img = f'data:image/png;base64,{farm["basemapB64"]}'
    basemap_js = "null"
    if basemap_img:
        basemap_js = json.dumps(basemap_img)

    farm_info = {k: v for k, v in farm.items() if k != "basemapB64"}
    farm_info_json = json.dumps(farm_info, ensure_ascii=False)

    xr0, xr1 = DEFAULT_X_RANGE

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Grower Field Weather Dashboard</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Helvetica Neue',Arial,sans-serif;background:#f4f5f7;color:#1e293b;line-height:1.5}}
.header{{background:#fff;border-bottom:1px solid #e2e8f0;padding:1rem 1.5rem;display:flex;flex-wrap:wrap;align-items:center;gap:1rem}}
.header h1{{font-size:1.35rem;font-weight:700;color:#0f172a}}
.header .subtitle{{font-size:0.9rem;color:#64748b;flex:1}}
.controls{{display:flex;flex-wrap:wrap;align-items:center;gap:0.75rem;margin-left:auto}}
.control-group{{position:relative}}
.control-group label{{font-size:0.75rem;font-weight:600;text-transform:uppercase;letter-spacing:0.03em;color:#64748b;display:block;margin-bottom:0.15rem}}
.dropdown-trigger{{display:flex;align-items:center;justify-content:space-between;min-width:140px;padding:0.35rem 0.65rem;background:#fff;border:1px solid #cbd5e1;border-radius:6px;cursor:pointer;font-size:0.82rem;color:#1e293b}}
.dropdown-trigger:hover{{border-color:#94a3b8}}
.dropdown-trigger:focus{{outline:2px solid #1f77b4;outline-offset:1px}}
.dropdown-menu{{display:none;position:absolute;top:100%;left:0;z-index:100;min-width:220px;max-height:260px;overflow-y:auto;background:#fff;border:1px solid #e2e8f0;border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,0.12);padding:0.35rem 0;margin-top:2px}}
.dropdown-menu.open{{display:block}}
.dropdown-menu .dd-action{{padding:0.4rem 0.75rem;font-size:0.8rem;font-weight:600;color:#1f77b4;cursor:pointer;border-bottom:1px solid #f1f5f9}}
.dropdown-menu .dd-action:hover{{background:#f8fafc}}
.dropdown-menu label{{display:flex;align-items:center;gap:0.5rem;padding:0.35rem 0.75rem;font-size:0.82rem;cursor:pointer}}
.dropdown-menu label:hover{{background:#f1f5f9}}
.dropdown-menu input[type="checkbox"]{{accent-color:#1f77b4}}
.nodata-label{{color:#94a3b8;font-style:italic;font-size:0.75rem}}
.summary-text{{font-size:0.82rem;color:#475569;margin-left:0.5rem}}
.btn-reset{{padding:0.35rem 0.85rem;background:#fff;border:1px solid #cbd5e1;border-radius:6px;cursor:pointer;font-size:0.82rem;color:#475569}}
.btn-reset:hover{{background:#f1f5f9;border-color:#94a3b8}}
.main{{display:flex;flex-direction:row;height:calc(100vh - 64px)}}
.map-pane{{flex:1;min-width:0}}
.chart-pane{{flex:1;min-width:0;display:flex;flex-direction:column}}
.chart{{flex:1;min-height:0}}
.empty-state{{display:flex;align-items:center;justify-content:center;height:100%;color:#94a3b8;font-size:0.95rem;font-style:italic}}
#dash-error{{display:none;background:#fef2f2;color:#991b1b;border:1px solid #fecaca;border-radius:6px;padding:0.75rem 1rem;margin:0.5rem 1.5rem;font-family:monospace;font-size:0.82rem;white-space:pre-wrap;overflow:auto;max-height:200px}}
#dash-error.visible{{display:block}}
@media (max-width:860px){{.main{{flex-direction:column;height:auto}}.map-pane{{height:50vh}}.chart{{height:40vh}}}}
</style>
</head>
<body>
<div class="header">
  <h1>Grower Field Weather Dashboard</h1>
  <div class="subtitle">{farm["farmName"]} &middot; {farm["growerId"]}</div>
  <div class="controls">
    <div class="control-group">
      <label>Fields</label>
      <div class="dropdown-trigger" id="fieldDropdownTrigger" tabindex="0" role="button" aria-haspopup="listbox">All fields</div>
      <div class="dropdown-menu" id="fieldDropdownMenu" role="listbox"></div>
    </div>
    <div class="control-group">
      <label>Years</label>
      <div class="dropdown-trigger" id="yearDropdownTrigger" tabindex="0" role="button" aria-haspopup="listbox"></div>
      <div class="dropdown-menu" id="yearDropdownMenu" role="listbox"></div>
    </div>
    <span class="summary-text" id="selectionSummary"></span>
    <button class="btn-reset" id="resetBtn">Reset view</button>
  </div>
</div>
<div id="dash-error"></div>
<div class="main">
  <div class="map-pane" id="mapContainer"></div>
  <div class="chart-pane">
    <div class="chart" id="gddChart"><div class="empty-state">No data available for the selected filters</div></div>
    <div class="chart" id="rainChart"><div class="empty-state">No data available for the selected filters</div></div>
  </div>
</div>
<script type="text/plain" id="plotly-source">
{plotly_js}
</script>
<script>
function _dashErr(msg){{
  var d=document.getElementById('dash-error');
  if(d){{d.className='visible';d.textContent+=msg+'\\n'}}
}}
window.onerror=function(m,u,l,c,e){{
  _dashErr(e&&e.stack?e.stack:(m||'')+' at '+u+':'+l);
}};
(function(){{
var codeEl=document.getElementById('plotly-source');
if(codeEl){{var s=document.createElement('script');s.textContent=codeEl.textContent;document.head.appendChild(s)}}
if(typeof Plotly==='undefined'){{
  _dashErr('Plotly library failed to load. Check browser console for details.');return;
}};
try{{
var DASHBOARD_FIELDS = {fields_json};
var DASHBOARD_WEATHER = {weather_json};
var BASEMAP_B64 = {basemap_js};
var FARM_INFO = {farm_info_json};
(function(){{
var SELECTED_FIELDS=new Set();
var SELECTED_YEARS=new Set();
var sharedXRange=null;
var updatingGdd=false;
var updatingRain=false;

function getFieldYears(fid){{var y=[];for(var i=0;i<DASHBOARD_WEATHER.length;i++){{if(DASHBOARD_WEATHER[i].fieldId===fid&&y.indexOf(DASHBOARD_WEATHER[i].year)===-1)y.push(DASHBOARD_WEATHER[i].year)}}return y.sort()}}
function getAllYears(){{var s=new Set();for(var i=0;i<DASHBOARD_WEATHER.length;i++)s.add(DASHBOARD_WEATHER[i].year);return Array.from(s).sort()}}
function hasWeather(fid){{for(var i=0;i<DASHBOARD_FIELDS.length;i++){{if(DASHBOARD_FIELDS[i].fieldId===fid)return DASHBOARD_FIELDS[i].hasWeatherData}}return false}}
function fieldName(fid){{for(var i=0;i<DASHBOARD_FIELDS.length;i++){{if(DASHBOARD_FIELDS[i].fieldId===fid)return DASHBOARD_FIELDS[i].fieldName}}return fid}}
function fieldColor(fid){{for(var i=0;i<DASHBOARD_FIELDS.length;i++){{if(DASHBOARD_FIELDS[i].fieldId===fid)return DASHBOARD_FIELDS[i].color}}return '#1f77b4'}}
function fieldAcres(fid){{for(var i=0;i<DASHBOARD_FIELDS.length;i++){{if(DASHBOARD_FIELDS[i].fieldId===fid)return DASHBOARD_FIELDS[i].acres}}return null}}
function getAllFieldIds(){{return DASHBOARD_FIELDS.map(function(f){{return f.fieldId}})}}

function buildFieldDropdown(){{
var menu=document.getElementById('fieldDropdownMenu');
var h='<div class="dd-action" data-action="select-all-fields">Select all</div><div class="dd-action" data-action="clear-all-fields">Clear all</div>';
for(var i=0;i<DASHBOARD_FIELDS.length;i++){{var f=DASHBOARD_FIELDS[i];var c=SELECTED_FIELDS.has(f.fieldId)?'checked':'';var nd=f.hasWeatherData?'':' <span class="nodata-label">(no data)</span>';h+='<label><input type="checkbox" class="field-cb" value="'+f.fieldId+'" '+c+'> '+f.fieldName+nd+'</label>'}}
menu.innerHTML=h}}

function buildYearDropdown(){{
var years=getAllYears();var menu=document.getElementById('yearDropdownMenu');
var h='<div class="dd-action" data-action="select-all-years">Select all</div><div class="dd-action" data-action="clear-all-years">Clear all</div>';
for(var i=0;i<years.length;i++){{var c=SELECTED_YEARS.has(years[i])?'checked':'';h+='<label><input type="checkbox" class="year-cb" value="'+years[i]+'" '+c+'> '+years[i]+'</label>'}}
menu.innerHTML=h}}

function updateDropdownLabels(){{
var ft=document.getElementById('fieldDropdownTrigger');var yt=document.getElementById('yearDropdownTrigger');
var fsel=SELECTED_FIELDS.size;var ysel=SELECTED_YEARS.size;
ft.textContent=fsel+' field'+(fsel!==1?'s':'');
yt.textContent=ysel+' year'+(ysel!==1?'s':'');
var s=document.getElementById('selectionSummary');
s.textContent=fsel>0||ysel>0?fsel+' field'+(fsel!==1?'s':'')+(ysel>0?', '+ysel+' year'+(ysel!==1?'s':''):''):'No fields or years selected'}}

function buildMap(){{
var fields=DASHBOARD_FIELDS;var traces=[];var xmin=Infinity,xmax=-Infinity,ymin=Infinity,ymax=-Infinity;
for(var i=0;i<fields.length;i++){{var f=fields[i];var sel=SELECTED_FIELDS.has(f.fieldId);var fc=f.color+(sel?'99':'22');var lw=sel?2.5:1;var polys=f.mercatorPolygons;if(!polys)continue;var ht='<b>'+f.fieldName+'</b>';if(f.acres!=null)ht+='<br>'+f.acres.toFixed(1)+' ac';
for(var j=0;j<polys.length;j++){{var ring=polys[j];var xs=[],ys=[];for(var k=0;k<ring.length;k++){{xs.push(ring[k][0]);ys.push(ring[k][1])}}
xs.push(xs[0]);ys.push(ys[0]);for(var k=0;k<xs.length;k++){{if(xs[k]<xmin)xmin=xs[k];if(xs[k]>xmax)xmax=xs[k];if(ys[k]<ymin)ymin=ys[k];if(ys[k]>ymax)ymax=ys[k]}}
traces.push({{x:xs,y:ys,mode:'lines',fill:'toself',fillcolor:fc,line:{{color:f.color,width:lw}},hoverinfo:'text',hovertext:ht,showlegend:false,name:f.fieldId,customdata:[f.fieldId],type:'scatter'}})}}}}

var layout={{dragmode:'pan',hovermode:'closest',xaxis:{{visible:false,scaleanchor:'y',scaleratio:1,range:[xmin===Infinity?-20037508.34:xmin,xmax===-Infinity?20037508.34:xmax]}},yaxis:{{visible:false,range:[ymin===Infinity?-20037508.34:ymin,ymax===-Infinity?20037508.34:ymax]}},margin:{{l:0,r:0,t:0,b:0,pad:0}},paper_bgcolor:'#f4f5f7',plot_bgcolor:'#f4f5f7',images:[],uirevision:'map-static'}};
if(BASEMAP_B64){{var bxmin=layout.xaxis.range[0],bxmax=layout.xaxis.range[1];var bymin=layout.yaxis.range[0],bymax=layout.yaxis.range[1];layout.images=[{{source:BASEMAP_B64,xref:'x',yref:'y',x:bxmin,y:bymax,sizex:bxmax-bxmin,sizey:bymax-bymin,sizing:'stretch',layer:'below',opacity:1}}]}}
Plotly.newPlot('mapContainer',traces,layout,{{responsive:true,displayModeBar:false,staticPlot:false}});
document.getElementById('mapContainer').on('plotly_click',function(eventData){{if(eventData.points&&eventData.points.length>0){{var d=eventData.points[0].customdata;var fid=Array.isArray(d)?d[0]:d;if(fid)toggleField(fid)}}}})}}

function updateMap(){{
var selectedArr=Array.from(SELECTED_FIELDS);
if(selectedArr.length===0){{var allFids=getAllFieldIds();for(var i=0;i<allFids.length;i++)SELECTED_FIELDS.add(allFids[i]);buildFieldDropdown();updateDropdownLabels();selectedArr=Array.from(SELECTED_FIELDS)}}
var traces=[];var xmin=Infinity,xmax=-Infinity,ymin=Infinity,ymax=-Infinity;
for(var i=0;i<DASHBOARD_FIELDS.length;i++){{var f=DASHBOARD_FIELDS[i];var sel=SELECTED_FIELDS.has(f.fieldId);var fc=f.color+(sel?'99':'22');var lw=sel?2.5:1;var polys=f.mercatorPolygons;if(!polys)continue;var ht='<b>'+f.fieldName+'</b>';if(f.acres!=null)ht+='<br>'+f.acres.toFixed(1)+' ac';
for(var j=0;j<polys.length;j++){{var ring=polys[j];var xs=[],ys=[];for(var k=0;k<ring.length;k++){{xs.push(ring[k][0]);ys.push(ring[k][1])}}
xs.push(xs[0]);ys.push(ys[0]);for(var k=0;k<xs.length;k++){{if(xs[k]<xmin)xmin=xs[k];if(xs[k]>xmax)xmax=xs[k];if(ys[k]<ymin)ymin=ys[k];if(ys[k]>ymax)ymax=ys[k]}}
traces.push({{x:xs,y:ys,mode:'lines',fill:'toself',fillcolor:fc,line:{{color:f.color,width:lw}},hoverinfo:'text',hovertext:ht,showlegend:false,name:f.fieldId,type:'scatter'}})}}}}
var px=(xmax-xmin)*0.2||1;var py=(ymax-ymin)*0.2||1;var rx=[xmin-px,xmax+px];var ry=[ymin-py,ymax+py];
var update={{data:traces,layout:{{xaxis:{{range:rx,visible:false,scaleanchor:'y',scaleratio:1}},yaxis:{{range:ry,visible:false}},images:BASEMAP_B64?[{{source:BASEMAP_B64,xref:'x',yref:'y',x:xmin,y:ymax,sizex:xmax-xmin,sizey:ymax-ymin,sizing:'stretch',layer:'below'}}]:[]}}}};
Plotly.react('mapContainer',update.data,update.layout,{{responsive:true,displayModeBar:false}})}}

function updateMapExtentForSelected(){{
var xmin=Infinity,xmax=-Infinity,ymin=Infinity,ymax=-Infinity;
for(var i=0;i<DASHBOARD_FIELDS.length;i++){{var f=DASHBOARD_FIELDS[i];if(!SELECTED_FIELDS.has(f.fieldId))continue;var polys=f.mercatorPolygons;if(!polys)continue;for(var j=0;j<polys.length;j++){{for(var k=0;k<polys[j].length;k++){{var px=polys[j][k][0],py=polys[j][k][1];if(px<xmin)xmin=px;if(px>xmax)xmax=px;if(py<ymin)ymin=py;if(py>ymax)ymax=py}}}}}}
if(!isFinite(xmin))return;var px=(xmax-xmin)*0.2||1;var py=(ymax-ymin)*0.2||1;
Plotly.relayout('mapContainer',{{'xaxis.range':[xmin-px,xmax+px],'yaxis.range':[ymin-py,ymax+py]}})}}

function buildGddChart(){{
var traces=[];
for(var i=0;i<DASHBOARD_WEATHER.length;i++){{var w=DASHBOARD_WEATHER[i];if(!SELECTED_FIELDS.has(w.fieldId)||!SELECTED_YEARS.has(w.year))continue;var c=fieldColor(w.fieldId);var n=fieldName(w.fieldId)+' '+w.year;var doys=w.daily.map(function(d){{return d.dayOfYear}});var gdds=w.daily.map(function(d){{return d.cumulativeGdd}});var dates=w.daily.map(function(d){{return d.date}});
traces.push({{x:doys,y:gdds,mode:'lines',name:n,line:{{color:c,width:2}},customdata:dates,hovertemplate:'<b>'+n+'</b><br>Date: %{{customdata}}<br>Day: %{{x}}<br>Cumulative GDD: %{{y:.1f}}<extra></extra>',type:'scatter'}});
traces.push({{x:[w.lastFrostDoy,w.lastFrostDoy],y:[0,1],mode:'lines',name:n+' frost',line:{{color:c,width:1.5,dash:'dot'}},yref:'paper',showlegend:false,hovertemplate:'Last frost: '+w.lastFrostDate+'<extra></extra>',type:'scatter'}})}}
if(traces.length===0){{document.getElementById('gddChart').innerHTML='<div class="empty-state">No data available for the selected filters</div>';return}}
var l={{title:{{text:'Growing Degree Days',font:{{size:13}}}},xaxis:{{title:'Day of Year',dtick:30,range:sharedXRange||[{xr0},{xr1}]}},yaxis:{{title:'Cumulative GDD (base 10°C)'}},margin:{{l:55,r:20,t:35,b:45,pad:4}},legend:{{orientation:'h',y:1.02,x:0,xanchor:'left'}},paper_bgcolor:'#fff',plot_bgcolor:'#fff',hovermode:'closest',uirevision:'gdd-'+Array.from(SELECTED_FIELDS).sort().join(',')+'-'+Array.from(SELECTED_YEARS).sort().join(',')}};
Plotly.newPlot('gddChart',traces,l,{{responsive:true,displayModeBar:false}});
document.getElementById('gddChart').on('plotly_relayout',function(ev){{if(updatingGdd)return;if(ev&&ev['xaxis.range']){{updatingRain=true;sharedXRange=ev['xaxis.range'];try{{Plotly.relayout('rainChart',{{'xaxis.range':sharedXRange}})}}catch(e){{}}updatingRain=false}}}})}}

function buildRainChart(){{
var traces=[];
for(var i=0;i<DASHBOARD_WEATHER.length;i++){{var w=DASHBOARD_WEATHER[i];if(!SELECTED_FIELDS.has(w.fieldId)||!SELECTED_YEARS.has(w.year))continue;var c=fieldColor(w.fieldId);var n=fieldName(w.fieldId)+' '+w.year;var doys=w.daily.map(function(d){{return d.dayOfYear}});var dr=w.daily.map(function(d){{return d.dailyRainfallIn}});var cr=w.daily.map(function(d){{return d.cumulativeRainfallIn}});var dates=w.daily.map(function(d){{return d.date}});
traces.push({{x:doys,y:dr,type:'bar',name:n+' daily',marker:{{color:c,opacity:0.25}},yaxis:'y',customdata:dates,hovertemplate:'<b>'+n+'</b><br>Date: %{{customdata}}<br>Day: %{{x}}<br>Daily rain: %{{y:.2f}} in<extra></extra>'}});
traces.push({{x:doys,y:cr,mode:'lines',name:n+' cumulative',line:{{color:c,width:2}},yaxis:'y2',customdata:dates,hovertemplate:'<b>'+n+'</b><br>Date: %{{customdata}}<br>Day: %{{x}}<br>Cumulative rain: %{{y:.2f}} in<extra></extra>',type:'scatter'}})}}
if(traces.length===0){{document.getElementById('rainChart').innerHTML='<div class="empty-state">No data available for the selected filters</div>';return}}
var l={{title:{{text:'Rainfall',font:{{size:13}}}},xaxis:{{title:'Day of Year',dtick:30,range:sharedXRange||[{xr0},{xr1}]}},yaxis:{{title:'Daily rainfall (in)',side:'left'}},yaxis2:{{title:'Cumulative rainfall (in)',overlaying:'y',side:'right'}},margin:{{l:55,r:55,t:35,b:45,pad:4}},legend:{{orientation:'h',y:1.02,x:0,xanchor:'left'}},paper_bgcolor:'#fff',plot_bgcolor:'#fff',hovermode:'closest',barmode:'overlay',uirevision:'rain-'+Array.from(SELECTED_FIELDS).sort().join(',')+'-'+Array.from(SELECTED_YEARS).sort().join(',')}};
Plotly.newPlot('rainChart',traces,l,{{responsive:true,displayModeBar:false}});
document.getElementById('rainChart').on('plotly_relayout',function(ev){{if(updatingRain)return;if(ev&&ev['xaxis.range']){{updatingGdd=true;sharedXRange=ev['xaxis.range'];try{{Plotly.relayout('gddChart',{{'xaxis.range':sharedXRange}})}}catch(e){{}}updatingGdd=false}}}})}}

function toggleField(fid){{if(SELECTED_FIELDS.has(fid))SELECTED_FIELDS.delete(fid);else SELECTED_FIELDS.add(fid);buildFieldDropdown();updateDropdownLabels();updateMap();updateMapExtentForSelected();rebuildCharts()}}
function selectAllFields(){{SELECTED_FIELDS=new Set(getAllFieldIds());buildFieldDropdown();updateDropdownLabels();updateMap();rebuildCharts()}}
function clearAllFields(){{SELECTED_FIELDS=new Set();buildFieldDropdown();updateDropdownLabels();updateMap();rebuildCharts()}}
function selectAllYears(){{SELECTED_YEARS=new Set(getAllYears());buildYearDropdown();updateDropdownLabels();rebuildCharts()}}
function clearAllYears(){{SELECTED_YEARS=new Set();buildYearDropdown();updateDropdownLabels();rebuildCharts()}}
function rebuildCharts(){{buildGddChart();buildRainChart()}}

function initDropdowns(){{
var ft=document.getElementById('fieldDropdownTrigger');var fm=document.getElementById('fieldDropdownMenu');
ft.addEventListener('click',function(e){{e.stopPropagation();var io=fm.classList.contains('open');document.querySelectorAll('.dropdown-menu.open').forEach(function(m){{m.classList.remove('open')}});if(!io)fm.classList.add('open')}});
fm.addEventListener('change',function(e){{var cb=e.target;if(cb.classList.contains('field-cb')){{if(cb.checked)SELECTED_FIELDS.add(cb.value);else SELECTED_FIELDS.delete(cb.value);updateDropdownLabels();updateMap();updateMapExtentForSelected();rebuildCharts()}}}});
fm.addEventListener('click',function(e){{var a=e.target.getAttribute('data-action');if(a==='select-all-fields'){{selectAllFields();fm.classList.remove('open')}}else if(a==='clear-all-fields'){{clearAllFields();fm.classList.remove('open')}}}});
var yt=document.getElementById('yearDropdownTrigger');var ym=document.getElementById('yearDropdownMenu');
yt.addEventListener('click',function(e){{e.stopPropagation();var io=ym.classList.contains('open');document.querySelectorAll('.dropdown-menu.open').forEach(function(m){{m.classList.remove('open')}});if(!io)ym.classList.add('open')}});
ym.addEventListener('change',function(e){{var cb=e.target;if(cb.classList.contains('year-cb')){{if(cb.checked)SELECTED_YEARS.add(parseInt(cb.value,10));else SELECTED_YEARS.delete(parseInt(cb.value,10));updateDropdownLabels();rebuildCharts()}}}});
ym.addEventListener('click',function(e){{var a=e.target.getAttribute('data-action');if(a==='select-all-years'){{selectAllYears();ym.classList.remove('open')}}else if(a==='clear-all-years'){{clearAllYears();ym.classList.remove('open')}}}});
document.addEventListener('click',function(){{document.querySelectorAll('.dropdown-menu.open').forEach(function(m){{m.classList.remove('open')}})}});
document.getElementById('resetBtn').addEventListener('click',function(){{resetView()}})}}

function resetView(){{
SELECTED_FIELDS=new Set(getAllFieldIds());var allYears=getAllYears();
if(allYears.indexOf(2025)!==-1)SELECTED_YEARS=new Set([2025]);else if(allYears.length>0)SELECTED_YEARS=new Set([allYears[allYears.length-1]]);else SELECTED_YEARS=new Set();
sharedXRange=null;buildFieldDropdown();buildYearDropdown();updateDropdownLabels();updateMap();rebuildCharts()}}

SELECTED_FIELDS=new Set(getAllFieldIds());
var allYears=getAllYears();
if(allYears.indexOf(2025)!==-1)SELECTED_YEARS=new Set([2025]);else if(allYears.length>0)SELECTED_YEARS=new Set([allYears[allYears.length-1]]);else SELECTED_YEARS=new Set();
buildFieldDropdown();buildYearDropdown();updateDropdownLabels();initDropdowns();buildMap();buildGddChart();buildRainChart();
}})();
}}catch(e){{_dashErr(e&&e.stack?e.stack:e.message)}}}})();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main generator entry point
# ---------------------------------------------------------------------------


def generate_dashboard(
    farm_dir_path: str | Path | None = None,
    output_path: str | Path | None = None,
    growers_dir: str | Path | None = None,
    data_root: str | Path | None = None,
    no_basemap: bool = False,
) -> Path:
    farm_dir = discover_farm_dir(
        farm_dir=farm_dir_path,
        growers_dir=growers_dir,
        data_root=data_root,
    )

    log.info("Resolved farm directory: %s", farm_dir)

    runtime_base = _find_runtime_base(farm_dir)
    shared_root = (runtime_base / "shared") if runtime_base is not None else None

    fields_gdf = load_farm_boundaries(farm_dir)
    log.info("Loaded %d field boundaries", len(fields_gdf))

    data = build_dashboard_data(farm_dir, fields_gdf, no_basemap, shared_root)
    num_weather = len(data["weatherByFieldYear"])
    log.info(
        "Dashboard data: %d fields, %d field-year weather records",
        len(data["fields"]),
        num_weather,
    )

    plotly_js = vendor_plotly(
        shared_root or Path(tempfile.mkdtemp())
    )

    html = render_dashboard_html(data, plotly_js)

    if output_path is not None:
        out = Path(str(output_path)).expanduser().resolve()
    else:
        farm_id = data["farm"]["farmId"]
        out = farm_dir / "derived" / "dashboards" / f"{farm_id}_dashboard.html"

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(
        ".tmp_" + hashlib.md5(str(out).encode()).hexdigest()[:8]
    )
    try:
        tmp.write_text(html, encoding="utf-8")
        tmp.replace(out)
    finally:
        if tmp.exists():
            tmp.unlink(missing_ok=True)

    log.info("Dashboard written to %s (%.1f KB)", out, out.stat().st_size / 1024)
    return out


def _find_runtime_base(farm_dir: Path) -> Path | None:
    for parent in [farm_dir] + list(farm_dir.parents):
        shared_candidate = parent / "shared"
        if shared_candidate.is_dir():
            return parent
    return None
