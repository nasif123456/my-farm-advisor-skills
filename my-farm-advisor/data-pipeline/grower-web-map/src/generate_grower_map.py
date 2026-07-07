#!/usr/bin/env python3
# pyright: reportMissingImports=false, reportArgumentType=false, reportCallIssue=false, reportAttributeAccessIssue=false
"""Generate a self-contained Leaflet HTML web map for each grower.

Scans the runtime grower tree, reads farm-level field_boundaries.geojson
files, and produces a grower_map.html per grower under
growers/<grower>/derived/maps/.

Enhancements include SSURGO soil data, weather summaries, NDVI peak
values, hover highlighting, farm legend, and total acreage.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import geopandas as gpd

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))
sys.path.insert(0, str(SCRIPTS_DIR / "lib"))

from paths import DATA_ROOT, GROWERS_ROOT, farm_boundary_path, farm_dir, field_dir


HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }}
#container {{ display: flex; height: 100vh; }}
#sidebar {{
  width: 280px; background: #f8f9fa; border-right: 1px solid #ddd;
  padding: 16px; overflow-y: auto; flex-shrink: 0;
}}
#sidebar h2 {{ font-size: 1.1em; margin-bottom: 4px; color: #1B5E20; }}
.summary {{ font-size: 0.85em; color: #555; margin-bottom: 14px; }}
.section-title {{ font-size: 0.75em; color: #888; text-transform: uppercase; letter-spacing: 0.5px; margin: 12px 0 6px; }}
.legend-item {{ display: flex; align-items: center; gap: 8px; font-size: 0.85em; padding: 3px 0; }}
.legend-color {{ width: 14px; height: 14px; border-radius: 3px; flex-shrink: 0; border: 1px solid rgba(0,0,0,0.1); }}
#field-list {{ list-style: none; }}
#field-list li {{
  padding: 6px 8px; margin: 2px 0; border-radius: 4px;
  cursor: pointer; font-size: 0.85em; background: #fff;
  border: 1px solid #e0e0e0; transition: background 0.15s;
}}
#field-list li:hover {{ background: #e8f5e9; border-color: #66bb6a; }}
#field-list li .field-name {{ font-weight: 600; color: #1B5E20; }}
#field-list li .field-meta {{ font-size: 0.8em; color: #888; }}
#map {{ flex: 1; }}
.leaflet-popup-content {{ font-size: 0.9em; line-height: 1.5; min-width: 180px; }}
.popup-label {{ font-weight: 600; color: #333; }}
.popup-divider {{ margin: 6px 0; border: none; border-top: 1px solid #eee; }}
</style>
</head>
<body>
<div id="container">
<div id="sidebar">
<h2>{grower_display}</h2>
<p class="summary">{farm_count} farm{farm_count_plural} &middot; {field_count} field{field_count_plural} &middot; {total_acres} ac</p>
{legend_items}
<div class="section-title">Fields</div>
<ul id="field-list">
{field_list_items}
</ul>
</div>
<div id="map"></div>
</div>
<script>
var map = L.map('map', {{ zoomControl: true }});

var satellite = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{{z}}/{{y}}/{{x}}', {{
  attribution: '&copy; Esri, USDA, USGS, AEX, GeoEye, Getmapping, Aerogrid, IGN, IGP, UPR-EGP, and the GIS User Community',
  maxZoom: 19,
}});

var osm = L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  maxZoom: 19,
}});

satellite.addTo(map);

var baseMaps = {{
  "Satellite": satellite,
  "Street Map": osm,
}};

L.control.layers(baseMaps).addTo(map);

var geojsonData = {geojson};

var fieldLayers = [];
var bounds = L.latLngBounds();
var allLayers = [];

var fields = L.geoJSON(geojsonData, {{
  style: function(feature) {{
    return {{
      color: feature.properties._color || '#2E7D32',
      weight: 2,
      fillOpacity: 0.35,
    }};
  }},
  onEachFeature: function(feature, layer) {{
    allLayers.push(layer);
    fieldLayers.push({{
      id: feature.properties.field_id,
      layer: layer,
    }});
    if (layer.getBounds) {{
      bounds.extend(layer.getBounds());
    }}

    var p = feature.properties;
    var name = p.display_name || p.field_id || '';
    var farm = p._farm_name || '';
    var grower = p._grower_name || '';
    var area = p.area_acres ? p.area_acres.toFixed(1) + ' ac' : '';
    var county = p.county_name || '';

    var popup = '<b>' + name + '</b><br>' +
      'Farm: ' + farm + ' | ' + grower + '<br>' +
      area + (county ? ' | ' + county : '');

    if (p._ssurgo_soil) {{
      popup += '<hr class="popup-divider">' +
        '<span class="popup-label">Soil:</span> ' + p._ssurgo_soil +
        (p._ssurgo_drainage ? ' (' + p._ssurgo_drainage + ')' : '') + '<br>' +
        '<span class="popup-label">OM:</span> ' + (p._ssurgo_om != null ? p._ssurgo_om + '%' : '--') +
        '  <span class="popup-label">pH:</span> ' + (p._ssurgo_ph != null ? p._ssurgo_ph : '--');
    }}

    if (p._weather_temp != null) {{
      popup += '<hr class="popup-divider">' +
        '<span class="popup-label">Temp:</span> ' + p._weather_temp + '&deg;C' +
        '  <span class="popup-label">Rain:</span> ' + p._weather_precip + ' mm/yr';
    }}

    if (p._ndvi_corn != null || p._ndvi_soy != null) {{
      popup += '<hr class="popup-divider">' +
        '<span class="popup-label">NDVI peak:</span>&nbsp;' +
        (p._ndvi_corn != null ? 'Corn ' + p._ndvi_corn : '') +
        (p._ndvi_corn != null && p._ndvi_soy != null ? ' | ' : '') +
        (p._ndvi_soy != null ? 'Soy ' + p._ndvi_soy : '');
    }}

    layer.bindPopup(popup);

    layer.on({{
      mouseover: function(e) {{
        var l = e.target;
        l.setStyle({{ weight: 4, fillOpacity: 0.5 }});
        l.bringToFront();
      }},
      mouseout: function(e) {{
        fields.resetStyle(e.target);
      }},
    }});
  }}
}}).addTo(map);

if (bounds.isValid()) {{
  map.fitBounds(bounds, {{ padding: [30, 30] }});
}}

function zoomToField(fieldId) {{
  for (var i = 0; i < fieldLayers.length; i++) {{
    if (fieldLayers[i].id === fieldId) {{
      var layer = fieldLayers[i].layer;
      if (layer.getBounds) {{
        map.fitBounds(layer.getBounds(), {{ padding: [30, 30] }});
      }} else if (layer.getLatLng) {{
        map.setView(layer.getLatLng(), 16);
      }}
      layer.openPopup();
      break;
    }}
  }}
}}
</script>
</body>
</html>"""


FARM_COLORS = [
    "#2E7D32",
    "#1565C0",
    "#E65100",
    "#6A1B9A",
    "#00838F",
    "#C62828",
    "#F9A825",
    "#4E342E",
    "#37474F",
    "#558B2F",
]


def _safe_float(val: str | None) -> float | None:
    if val is None or val.strip() == "":
        return None
    try:
        return float(val)
    except ValueError:
        return None


def _discover_growers() -> list[str]:
    return sorted(
        d.name for d in GROWERS_ROOT.iterdir() if d.is_dir() and (d / "farms").is_dir()
    )


def _discover_farms(grower_slug: str) -> list[str]:
    farms_dir = GROWERS_ROOT / grower_slug / "farms"
    return sorted(d.name for d in farms_dir.iterdir() if d.is_dir())


def _load_farm_json(grower_slug: str, farm_slug: str) -> dict:
    path = farm_dir(grower_slug, farm_slug) / "farm.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _load_grower_json(grower_slug: str) -> dict:
    path = GROWERS_ROOT / grower_slug / "grower.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _read_ssurgo_summary(field_dir: Path) -> dict:
    path = field_dir / "soil" / "ssurgo_summary.csv"
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            return {
                "dominant_soil": row.get("dominant_soil", ""),
                "drainage_class": row.get("drainage_class", ""),
                "avg_om_pct": _safe_float(row.get("avg_om_pct")),
                "avg_ph": _safe_float(row.get("avg_ph")),
            }
    return {}


def _read_weather_summary(field_dir: Path) -> dict:
    path = field_dir / "weather" / "daily_weather.csv"
    if not path.exists():
        return {}
    count = 0
    total_temp = 0.0
    total_precip = 0.0
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                total_temp += float(row.get("T2M", 0))
                total_precip += float(row.get("PRECTOTCORR", 0))
                count += 1
            except (ValueError, TypeError):
                continue
    if count == 0:
        return {}
    return {
        "avg_temp_c": round(total_temp / count, 1),
        "total_precip_mm": round(total_precip, 0),
    }


def _read_ndvi_summary(field_dir: Path) -> dict:
    path = field_dir / "derived" / "summaries" / "ndvi_card_summary.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        cards = data.get("cards", {})
        result: dict[str, float | None] = {"ndvi_corn_peak": None, "ndvi_soy_peak": None}
        corn_peak = cards.get("corn_peak_95", {})
        soy_peak = cards.get("soybean_peak_95", {})
        if isinstance(corn_peak, dict):
            mv = corn_peak.get("mean_ndvi")
            if mv is not None:
                result["ndvi_corn_peak"] = round(float(mv), 3)
        if isinstance(soy_peak, dict):
            mv = soy_peak.get("mean_ndvi")
            if mv is not None:
                result["ndvi_soy_peak"] = round(float(mv), 3)
        return result
    except (json.JSONDecodeError, TypeError, ValueError):
        return {}


def _build_field_list_html(items: list[dict]) -> str:
    lines = []
    for item in items:
        name = item.get("display_name") or item.get("field_id", "?")
        farm = item.get("_farm_name", "")
        area = item.get("area_acres")
        area_str = f"{area:.1f} ac" if area else ""
        fid = item.get("field_id", "")
        lines.append(
            f'<li onclick="zoomToField({json.dumps(fid)})">'
            f'<div class="field-name">{name}</div>'
            f'<div class="field-meta">{farm}{" · " + area_str if area_str else ""}</div>'
            f"</li>"
        )
    return "\n".join(lines)


def _build_legend_html(farm_stats: list[dict]) -> str:
    if not farm_stats:
        return ""
    lines = ['<div class="section-title">Farms</div>']
    for stat in farm_stats:
        color = stat.get("color", "#999")
        name = stat.get("name", "?")
        fields = stat.get("field_count", 0)
        acres = stat.get("total_acres", 0)
        label = f"{name} ({fields} field{'s' if fields != 1 else ''}, {acres:.1f} ac)"
        lines.append(
            f'<div class="legend-item">'
            f'<div class="legend-color" style="background:{color}"></div>'
            f"<span>{label}</span>"
            f"</div>"
        )
    return "\n".join(lines)


def generate_grower_map(grower_slug: str) -> Path:
    grower_meta = _load_grower_json(grower_slug)
    grower_display = grower_meta.get("display_name", grower_slug)

    all_features: list[dict] = []
    field_list_items: list[dict] = []
    farm_stats: list[dict] = []

    farms = _discover_farms(grower_slug)
    if not farms:
        print(f"  [skip] no farms found for grower {grower_slug}")
        return None

    for idx, farm_slug in enumerate(farms):
        boundary_path = farm_boundary_path(grower_slug, farm_slug)
        if not boundary_path.exists():
            print(f"  [skip] no boundary file for {grower_slug}/{farm_slug}")
            continue

        farm_meta = _load_farm_json(grower_slug, farm_slug)
        farm_display = farm_meta.get("display_name", farm_slug)
        color = FARM_COLORS[idx % len(FARM_COLORS)]
        farm_field_count = 0
        farm_acres = 0.0

        gdf = gpd.read_file(boundary_path)
        for _, row in gdf.iterrows():
            props = dict(row.drop("geometry").to_dict())
            fid = props.get("field_id", "")
            fs = field_dir(grower_slug, farm_slug, fid)
            ssurgo = _read_ssurgo_summary(fs)
            weather = _read_weather_summary(fs)
            ndvi = _read_ndvi_summary(fs)

            props["_color"] = color
            props["_farm_name"] = farm_display
            props["_grower_name"] = grower_display
            props["_ssurgo_soil"] = ssurgo.get("dominant_soil", "")
            props["_ssurgo_drainage"] = ssurgo.get("drainage_class", "")
            props["_ssurgo_om"] = ssurgo.get("avg_om_pct")
            props["_ssurgo_ph"] = ssurgo.get("avg_ph")
            props["_weather_temp"] = weather.get("avg_temp_c")
            props["_weather_precip"] = weather.get("total_precip_mm")
            props["_ndvi_corn"] = ndvi.get("ndvi_corn_peak")
            props["_ndvi_soy"] = ndvi.get("ndvi_soy_peak")

            geom = row.geometry
            feat = {
                "type": "Feature",
                "properties": props,
                "geometry": json.loads(gpd.GeoSeries([geom]).to_json())["features"][0][
                    "geometry"
                ],
            }
            all_features.append(feat)
            field_list_items.append(props)
            farm_field_count += 1
            farm_acres += float(props.get("area_acres", 0) or 0)

        farm_stats.append(
            {
                "name": farm_display,
                "color": color,
                "field_count": farm_field_count,
                "total_acres": round(farm_acres, 1),
            }
        )

    if not all_features:
        print(f"  [skip] no field features found for grower {grower_slug}")
        return None

    total_acres = round(sum(s["total_acres"] for s in farm_stats), 1)

    fc = {"type": "FeatureCollection", "features": all_features}
    geojson_str = json.dumps(fc, default=str)

    field_list_html = _build_field_list_html(field_list_items)
    legend_html = _build_legend_html(farm_stats)
    plural_farm = "s" if len(farms) != 1 else ""
    plural_field = "s" if len(all_features) != 1 else ""

    html = HTML_TEMPLATE.format(
        title=f"Fields - {grower_display}",
        grower_display=grower_display,
        farm_count=len(farms),
        farm_count_plural=plural_farm,
        field_count=len(all_features),
        field_count_plural=plural_field,
        total_acres=total_acres,
        legend_items=legend_html,
        field_list_items=field_list_html,
        geojson=geojson_str,
    )

    out_dir = GROWERS_ROOT / grower_slug / "derived" / "maps"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "grower_map.html"
    out_path.write_text(html, encoding="utf-8")
    print(f"  [ok]  {out_path}")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate grower-level interactive web maps from pipeline field boundaries."
    )
    parser.add_argument(
        "--grower-slug",
        default=None,
        help="Generate map for a single grower. If omitted, generates for all growers.",
    )
    args = parser.parse_args()

    if args.grower_slug:
        slugs = [args.grower_slug]
    else:
        slugs = _discover_growers()

    for slug in slugs:
        print(f"[grower] {slug}")
        generate_grower_map(slug)


if __name__ == "__main__":
    main()
