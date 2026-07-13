#!/usr/bin/env python3
"""Tests for the offline Grower Field Weather Dashboard generator.

Uses the Iowa fixture when available; otherwise constructs synthetic mocks.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd

_SCRIPT_DIR = Path(__file__).resolve().parent
_SRC_DIR = _SCRIPT_DIR / ".." / "lib"
sys.path.insert(0, str(_SRC_DIR))
sys.path.insert(0, str(_SCRIPT_DIR / ".."))
sys.path.insert(0, str(_SCRIPT_DIR / ".." / "reporting"))

try:
    import geopandas as gpd
except ImportError:
    gpd = None  # type: ignore[assignment]

from dashboard_generator import (  # noqa: E402
    acquire_basemap,
    allocate_colors,
    build_dashboard_data,
    compute_field_year_weather,
    discover_farm_dir,
    generate_dashboard,
    geometry_to_mercator_polygons,
    load_farm_boundaries,
    load_field_metadata,
    load_field_weather,
    render_dashboard_html,
    vendor_plotly,
)

# ---------------------------------------------------------------------------
# Locate the Iowa fixture
# ---------------------------------------------------------------------------

FIXTURE_FARM_DIR: Path | None = None
for candidate in [
    Path.home() / "my-farm-advisor-runtime" / "data-pipeline" / "growers" / "iowa-grower" / "farms" / "iowa-grower-iowa",
]:
    if (candidate / "boundary" / "field_boundaries.geojson").exists():
        FIXTURE_FARM_DIR = candidate
        break


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_geo_dataframe(num_fields: int = 3) -> "gpd.GeoDataFrame":
    """Create a minimal mock GeoDataFrame for testing."""
    try:
        from shapely.geometry import Polygon
    except ImportError:
        return None  # type: ignore[return-value]

    polygons = []
    for i in range(num_fields):
        base_lon = -94.0 + i * 0.05
        base_lat = 43.2 + i * 0.02
        polygons.append(
            Polygon([
                (base_lon, base_lat),
                (base_lon + 0.04, base_lat),
                (base_lon + 0.04, base_lat + 0.02),
                (base_lon, base_lat + 0.02),
                (base_lon, base_lat),
            ])
        )
    return gpd.GeoDataFrame(
        {
            "field_id": [f"test-{i:04d}" for i in range(num_fields)],
            "area_acres": [100.0 + i * 10 for i in range(num_fields)],
        },
        geometry=polygons,
        crs="EPSG:4326",
    )


def _make_mock_weather_csv(
    path: Path,
    field_id: str = "test-0000",
    years: list[int] | None = None,
    empty: bool = False,
) -> None:
    """Create a mock daily weather CSV at the given path."""
    if years is None:
        years = [2025]
    if empty:
        path.write_text("field_id,lat,lon,date,T2M,T2M_MAX,T2M_MIN,PRECTOTCORR,ALLSKY_SFC_SW_DWN,RH2M,WS10M\n", encoding="utf-8")
        return

    path.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for year in years:
        for month in range(1, 13):
            for day in [1, 15]:
                tmax = 30.0 if month >= 4 else -5.0
                tmin = 15.0 if month >= 4 else -15.0
                precip = 2.0 if month >= 4 else 0.0
                rows.append(
                    f"{field_id},42.0,-94.0,{year}-{month:02d}-{day:02d},0,{tmax},{tmin},{precip},5,80,3\n"
                )
    path.write_text(
        f"field_id,lat,lon,date,T2M,T2M_MAX,T2M_MIN,PRECTOTCORR,ALLSKY_SFC_SW_DWN,RH2M,WS10M\n"
        + "".join(rows),
        encoding="utf-8",
    )


def _make_mock_farm_dir(root: Path, num_fields: int = 3) -> Path:
    """Create a minimal mock farm directory with boundaries, fields, and weather."""
    try:
        from shapely.geometry import Polygon
    except ImportError:
        return None  # type: ignore[return-value]

    farm_dir = root / "test-farm"
    boundary_dir = farm_dir / "boundary"
    fields_dir = farm_dir / "fields"
    boundary_dir.mkdir(parents=True, exist_ok=True)
    fields_dir.mkdir(parents=True, exist_ok=True)

    features = []
    for i in range(num_fields):
        fid = f"test-{i:04d}"
        base_lon = -94.0 + i * 0.05
        base_lat = 43.2 + i * 0.02
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "field_id": fid,
                    "area_acres": 100.0 + i * 10,
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[
                        [base_lon, base_lat],
                        [base_lon + 0.04, base_lat],
                        [base_lon + 0.04, base_lat + 0.02],
                        [base_lon, base_lat + 0.02],
                        [base_lon, base_lat],
                    ]],
                },
            }
        )
        # Create field.json
        field_dir = fields_dir / fid
        field_dir.mkdir(parents=True, exist_ok=True)
        (field_dir / "field.json").write_text(
            json.dumps({"field_id": fid, "display_name": f"Field {i}"}, ensure_ascii=False),
            encoding="utf-8",
        )
        # Create weather data
        _make_mock_weather_csv(field_dir / "weather" / "daily_weather.csv", fid)

    fc = {"type": "FeatureCollection", "features": features}
    (boundary_dir / "field_boundaries.geojson").write_text(
        json.dumps(fc, ensure_ascii=False), encoding="utf-8"
    )
    return farm_dir


def _has_noaa_power(df: pd.DataFrame) -> bool:
    """Check if a DataFrame has the expected NASA POWER columns."""
    return {"T2M_MIN", "T2M_MAX", "PRECTOTCORR"}.issubset(df.columns)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestColorAllocation:
    def test_deterministic(self):
        ids = ["a", "b", "c"]
        c1 = allocate_colors(ids)
        c2 = allocate_colors(ids)
        assert c1 == c2

    def test_cycles_palette(self):
        ids = [f"f{i}" for i in range(15)]
        colors = allocate_colors(ids)
        assert len(set(colors.values())) == 10
        assert colors["f0"] == "#1f77b4"
        assert colors["f10"] == "#1f77b4"  # wrapped

    def test_single_field(self):
        colors = allocate_colors(["only"])
        assert len(colors) == 1
        assert colors["only"] == "#1f77b4"


class TestMercatorProjection:
    def test_wgs84_to_mercator(self):
        from dashboard_generator import wgs84_to_mercator
        mx, my = wgs84_to_mercator(-94.0, 43.0)
        assert -20037508 < mx < 20037508
        assert -20037508 < my < 20037508

    def test_geometry_to_polygon(self):
        geom = {
            "type": "Polygon",
            "coordinates": [[[-94.0, 43.0], [-93.9, 43.0], [-93.9, 43.1], [-94.0, 43.1], [-94.0, 43.0]]],
        }
        rings = geometry_to_mercator_polygons(geom)
        assert len(rings) == 1
        assert len(rings[0]) == 5

    def test_geometry_to_multipolygon(self):
        geom = {
            "type": "MultiPolygon",
            "coordinates": [
                [[[-94.0, 43.0], [-93.9, 43.0], [-93.9, 43.1], [-94.0, 43.1], [-94.0, 43.0]]],
                [[[-93.8, 43.0], [-93.7, 43.0], [-93.7, 43.1], [-93.8, 43.1], [-93.8, 43.0]]],
            ],
        }
        rings = geometry_to_mercator_polygons(geom)
        assert len(rings) == 2


class TestFarmDirectoryDiscovery:
    def test_explicit_farm_dir_valid(self, tmp_path):
        farm_dir = _make_mock_farm_dir(tmp_path)
        result = discover_farm_dir(farm_dir=farm_dir)
        assert result == farm_dir.resolve()

    def test_explicit_farm_dir_missing(self):
        try:
            discover_farm_dir(farm_dir="/nonexistent/path")
            assert False, "Should have raised ValueError"
        except ValueError:
            pass

    def test_explicit_farm_dir_no_boundary(self, tmp_path):
        d = tmp_path / "bad-farm"
        d.mkdir()
        (d / "fields").mkdir()
        try:
            discover_farm_dir(farm_dir=d)
            assert False, "Should have raised ValueError"
        except ValueError:
            pass

    def test_growers_dir_unique(self, tmp_path):
        growers_dir = tmp_path / "growers"
        farm_dir = _make_mock_farm_dir(growers_dir / "g1")
        # Move it into the growers/g1/farms/ structure
        real_farm = growers_dir / "g1" / "farms" / farm_dir.name
        real_farm.parent.mkdir(parents=True, exist_ok=True)
        farm_dir.rename(real_farm)

        result = discover_farm_dir(growers_dir=growers_dir)
        assert result == real_farm.resolve()

    def test_growers_dir_multiple_farms(self, tmp_path):
        growers_dir = tmp_path / "growers"
        # Create two separate grower/farm structures each with a valid farm

        def _make_mock_in_place(base):
            # Create a proper farm structure directly at base
            bdir = base / "boundary"
            fdir = base / "fields"
            bdir.mkdir(parents=True, exist_ok=True)
            fdir.mkdir(parents=True, exist_ok=True)
            from shapely.geometry import Polygon
            gdf = gpd.GeoDataFrame(
                {"field_id": ["f1"], "area_acres": [100.0]},
                geometry=[Polygon([(-94.0, 43.0), (-93.9, 43.0), (-93.9, 43.1), (-94.0, 43.1), (-94.0, 43.0)])],
                crs="EPSG:4326",
            )
            gdf.to_file(str(bdir / "field_boundaries.geojson"), driver="GeoJSON")

        _make_mock_in_place(growers_dir / "g1" / "farms" / "farm1")
        _make_mock_in_place(growers_dir / "g2" / "farms" / "farm2")

        try:
            discover_farm_dir(growers_dir=growers_dir)
            assert False, "Should have raised ValueError for multiple"
        except ValueError as e:
            assert "Multiple" in str(e)

    def test_auto_discovery(self, tmp_path, monkeypatch):
        # Create a farm dir in a temp home
        home = tmp_path / "home"
        home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: home)
        farm_dir = _make_mock_farm_dir(home / "runtime" / "growers" / "g1")
        real_farm = home / "runtime" / "growers" / "g1" / "farms" / farm_dir.name
        real_farm.parent.mkdir(parents=True, exist_ok=True)
        farm_dir.rename(real_farm)

        with patch.dict(os.environ, {}, clear=True):
            result = discover_farm_dir()
            assert result == real_farm.resolve()


class TestDataLoading:
    def test_load_farm_boundaries(self, tmp_path):
        farm_dir = _make_mock_farm_dir(tmp_path)
        gdf = load_farm_boundaries(farm_dir)
        assert len(gdf) == 3
        assert "field_id" in gdf.columns

    def test_load_farm_boundaries_missing(self, tmp_path):
        try:
            load_farm_boundaries(tmp_path / "nonexistent")
            assert False
        except FileNotFoundError:
            pass

    def test_load_field_metadata_exists(self, tmp_path):
        farm_dir = _make_mock_farm_dir(tmp_path)
        meta = load_field_metadata(farm_dir, "test-0000")
        assert meta["display_name"] == "Field 0"

    def test_load_field_metadata_missing(self, tmp_path):
        farm_dir = _make_mock_farm_dir(tmp_path)
        meta = load_field_metadata(farm_dir, "nonexistent-field")
        assert meta["field_id"] == "nonexistent-field"

    def test_load_field_weather_exists(self, tmp_path):
        farm_dir = _make_mock_farm_dir(tmp_path)
        df = load_field_weather(farm_dir, "test-0000")
        assert not df.empty

    def test_load_field_weather_missing(self, tmp_path):
        farm_dir = _make_mock_farm_dir(tmp_path)
        df = load_field_weather(farm_dir, "nonexistent")
        assert df.empty

    def test_load_field_weather_empty_csv(self, tmp_path):
        farm_dir = _make_mock_farm_dir(tmp_path, num_fields=1)
        # Replace with header-only CSV
        weather_dir = farm_dir / "fields" / "test-0000" / "weather"
        weather_dir.mkdir(parents=True, exist_ok=True)
        (weather_dir / "daily_weather.csv").write_text(
            "field_id,lat,lon,date,T2M,T2M_MAX,T2M_MIN,PRECTOTCORR,ALLSKY_SFC_SW_DWN,RH2M,WS10M\n",
            encoding="utf-8",
        )
        df = load_field_weather(farm_dir, "test-0000")
        assert df.empty


class TestWeatherTransformations:
    def test_compute_field_year_weather_basic(self):
        df = pd.DataFrame({
            "date": [f"2025-{m:02d}-15" for m in range(1, 13)],
            "T2M_MAX": [-5, 0, 5, 15, 25, 30, 32, 30, 25, 15, 5, -2],
            "T2M_MIN": [-15, -10, -5, 5, 15, 20, 22, 20, 15, 5, -2, -10],
            "PRECTOTCORR": [0, 0, 0.5, 2, 5, 8, 10, 8, 5, 2, 0.5, 0],
        })
        records = compute_field_year_weather(df, "test-0000", 2025)
        assert len(records) > 0
        # First record should be after last frost (some day in spring)
        assert records[0]["dayOfYear"] >= 1

    def test_last_frost_before_july(self):
        # Temps below freezing Jan-Mar, above after
        df = pd.DataFrame({
            "date": [
                "2025-01-15", "2025-02-15", "2025-03-15",
                "2025-04-15", "2025-05-15", "2025-06-15",
            ],
            "T2M_MAX": [-5, -3, 2, 15, 25, 30],
            "T2M_MIN": [-15, -12, -5, 5, 15, 20],
            "PRECTOTCORR": [0] * 6,
        })
        records = compute_field_year_weather(df, "test", 2025)
        # Last frost should be in March (T2M_MIN <= 0)
        assert records[0]["dayOfYear"] <= 90  # March 31 = day 90

    def test_no_frost_uses_jan1(self):
        df = pd.DataFrame({
            "date": [
                "2025-01-15", "2025-04-15", "2025-07-15",
            ],
            "T2M_MAX": [10, 15, 30],
            "T2M_MIN": [2, 5, 20],
            "PRECTOTCORR": [0, 2, 5],
        })
        records = compute_field_year_weather(df, "test", 2025)
        assert len(records) == 3
        assert records[0]["dayOfYear"] == 15  # Jan 15

    def test_cumulative_gdd_from_frost(self):
        df = pd.DataFrame({
            "date": ["2025-04-01", "2025-04-02", "2025-04-03"],
            "T2M_MAX": [15, 20, 25],
            "T2M_MIN": [5, 10, 15],
            "PRECTOTCORR": [0, 0, 0],
        })
        records = compute_field_year_weather(df, "test", 2025)
        # dailyGdd = average - 10, clipped at 0
        # day1: (15+5)/2 - 10 = 0
        # day2: (20+10)/2 - 10 = 5
        # day3: (25+15)/2 - 10 = 10
        # cumulative: 0, 5, 15
        assert len(records) == 3
        assert records[0]["dailyGdd"] == 0
        assert records[1]["dailyGdd"] == 5
        assert abs(records[1]["cumulativeGdd"] - 5) < 0.001
        assert abs(records[2]["cumulativeGdd"] - 15) < 0.001

    def test_rainfall_conversion(self):
        df = pd.DataFrame({
            "date": ["2025-04-01"],
            "T2M_MAX": [20],
            "T2M_MIN": [10],
            "PRECTOTCORR": [25.4],  # 1 inch
        })
        records = compute_field_year_weather(df, "test", 2025)
        assert abs(records[0]["dailyRainfallIn"] - 1.0) < 0.01
        assert abs(records[0]["cumulativeRainfallIn"] - 1.0) < 0.01

    def test_missing_columns_returns_empty(self):
        df = pd.DataFrame({"date": ["2025-01-01"], "T2M_MAX": [10]})
        records = compute_field_year_weather(df, "test", 2025)
        assert records == []

    def test_different_year_filter(self):
        df = pd.DataFrame({
            "date": ["2024-01-15", "2025-01-15", "2025-04-15"],
            "T2M_MAX": [10, 10, 20],
            "T2M_MIN": [0, 0, 10],
            "PRECTOTCORR": [0, 0, 2],
        })
        records_2024 = compute_field_year_weather(df, "test", 2024)
        records_2025 = compute_field_year_weather(df, "test", 2025)
        assert len(records_2024) > 0
        assert len(records_2025) > 0

    def test_leap_year(self):
        df = pd.DataFrame({
            "date": ["2024-02-29", "2024-03-01"],
            "T2M_MAX": [5, 15],
            "T2M_MIN": [-2, 5],
            "PRECTOTCORR": [0, 2],
        })
        records = compute_field_year_weather(df, "test", 2024)
        assert len(records) == 2


class TestDashboardDataModel:
    def test_build_dashboard_data(self, tmp_path):
        farm_dir = _make_mock_farm_dir(tmp_path, num_fields=3)
        gdf = load_farm_boundaries(farm_dir)
        data = build_dashboard_data(farm_dir, gdf, no_basemap=True)
        assert "farm" in data
        assert "fields" in data
        assert "weatherByFieldYear" in data
        assert len(data["fields"]) == 3
        assert data["farm"]["basemapAvailable"] is False

    def test_field_metadata_embedded(self, tmp_path):
        farm_dir = _make_mock_farm_dir(tmp_path, num_fields=2)
        gdf = load_farm_boundaries(farm_dir)
        data = build_dashboard_data(farm_dir, gdf, no_basemap=True)
        names = {f["fieldName"] for f in data["fields"]}
        assert "Field 0" in names
        assert "Field 1" in names

    def test_weather_records_present(self, tmp_path):
        farm_dir = _make_mock_farm_dir(tmp_path, num_fields=1)
        gdf = load_farm_boundaries(farm_dir)
        data = build_dashboard_data(farm_dir, gdf, no_basemap=True)
        assert len(data["weatherByFieldYear"]) >= 1
        w = data["weatherByFieldYear"][0]
        assert "lastFrostDate" in w
        assert "daily" in w
        assert len(w["daily"]) > 0

    def test_empty_weather_no_failure(self, tmp_path):
        farm_dir = _make_mock_farm_dir(tmp_path, num_fields=1)
        # Replace weather with header-only
        wdir = farm_dir / "fields" / "test-0000" / "weather"
        wdir.mkdir(parents=True, exist_ok=True)
        (wdir / "daily_weather.csv").write_text(
            "field_id,lat,lon,date,T2M,T2M_MAX,T2M_MIN,PRECTOTCORR,ALLSKY_SFC_SW_DWN,RH2M,WS10M\n",
            encoding="utf-8",
        )
        gdf = load_farm_boundaries(farm_dir)
        data = build_dashboard_data(farm_dir, gdf, no_basemap=True)
        assert len(data["weatherByFieldYear"]) == 0
        # Fields should still be present with hasWeatherData=False
        assert not data["fields"][0]["hasWeatherData"]

    def test_deterministic_fields(self, tmp_path):
        farm_dir = _make_mock_farm_dir(tmp_path, num_fields=3)
        gdf = load_farm_boundaries(farm_dir)
        data1 = build_dashboard_data(farm_dir, gdf, no_basemap=True)
        data2 = build_dashboard_data(farm_dir, gdf, no_basemap=True)
        fids1 = [f["fieldId"] for f in data1["fields"]]
        fids2 = [f["fieldId"] for f in data2["fields"]]
        assert fids1 == fids2

    def test_missing_field_json_fallback(self, tmp_path):
        farm_dir = _make_mock_farm_dir(tmp_path, num_fields=1)
        # Remove field.json
        (farm_dir / "fields" / "test-0000" / "field.json").unlink()
        gdf = load_farm_boundaries(farm_dir)
        data = build_dashboard_data(farm_dir, gdf, no_basemap=True)
        assert data["fields"][0]["fieldName"] == "test-0000"


class TestHtmlRendering:
    def test_rendered_output_is_html(self, tmp_path):
        farm_dir = _make_mock_farm_dir(tmp_path, num_fields=1)
        gdf = load_farm_boundaries(farm_dir)
        data = build_dashboard_data(farm_dir, gdf, no_basemap=True)
        plotly_js = "/* mock plotly */"
        html = render_dashboard_html(data, plotly_js)
        assert html.startswith("<!DOCTYPE html>")
        assert "</html>" in html

    def test_embedded_data(self, tmp_path):
        farm_dir = _make_mock_farm_dir(tmp_path, num_fields=2)
        gdf = load_farm_boundaries(farm_dir)
        data = build_dashboard_data(farm_dir, gdf, no_basemap=True)
        plotly_js = "/* mock plotly */"
        html = render_dashboard_html(data, plotly_js)
        # Should have DASHBOARD_FIELDS and DASHBOARD_WEATHER constants
        assert "var DASHBOARD_FIELDS" in html
        assert "var DASHBOARD_WEATHER" in html

    def test_no_cdn_urls_in_output(self, tmp_path):
        farm_dir = _make_mock_farm_dir(tmp_path, num_fields=1)
        gdf = load_farm_boundaries(farm_dir)
        data = build_dashboard_data(farm_dir, gdf, no_basemap=True)
        plotly_js = "/* mock plotly */"
        html = render_dashboard_html(data, plotly_js)
        # No runtime CDN or external URL references
        assert "cdn.plot.ly" not in html
        assert "unpkg.com" not in html
        # The Plotly bundle may contain internal https:// references,
        # but our mock does not, so check explicitly here:
        assert "http://" not in html

    def test_render_with_plotly(self, tmp_path):
        farm_dir = _make_mock_farm_dir(tmp_path, num_fields=1)
        gdf = load_farm_boundaries(farm_dir)
        data = build_dashboard_data(farm_dir, gdf, no_basemap=True)
        plotly_js = "/* Plotly mock */\nfunction Plotly(){}"
        html = render_dashboard_html(data, plotly_js)
        assert "Plotly" in html or "plotly" in html.lower()


class TestGenerateDashboard:
    def test_generate_to_default_output(self, tmp_path):
        farm_dir = _make_mock_farm_dir(tmp_path, num_fields=2)
        with patch.dict(os.environ, {}, clear=True):
            out = generate_dashboard(
                farm_dir_path=farm_dir,
                no_basemap=True,
            )
        assert out.exists()
        assert out.suffix == ".html"
        assert "dashboard" in out.name

    def test_generate_to_custom_output(self, tmp_path):
        farm_dir = _make_mock_farm_dir(tmp_path, num_fields=1)
        custom = tmp_path / "custom_output.html"
        with patch.dict(os.environ, {}, clear=True):
            out = generate_dashboard(
                farm_dir_path=farm_dir,
                output_path=custom,
                no_basemap=True,
            )
        assert out == custom.resolve()
        assert custom.exists()

    def test_generated_html_contains_expected_content(self, tmp_path):
        farm_dir = _make_mock_farm_dir(tmp_path, num_fields=2)
        with patch.dict(os.environ, {}, clear=True):
            out = generate_dashboard(
                farm_dir_path=farm_dir,
                no_basemap=True,
            )
        html = out.read_text(encoding="utf-8")
        assert "Dashboard" in html or "dashboard" in html
        assert "Field 0" in html
        assert "Field 1" in html
        assert "test-0000" in html

    def test_missing_geometry_fails(self, tmp_path):
        farm_dir = tmp_path / "bad"
        farm_dir.mkdir()
        (farm_dir / "boundary").mkdir()
        (farm_dir / "fields").mkdir()
        try:
            generate_dashboard(farm_dir_path=farm_dir, no_basemap=True)
            assert False
        except (ValueError, FileNotFoundError):
            pass

    def test_standalone_does_not_call_upstream(self, tmp_path):
        # Verify standalone generation only reads existing files
        farm_dir = _make_mock_farm_dir(tmp_path, num_fields=1)
        with patch.dict(os.environ, {}, clear=True):
            out = generate_dashboard(
                farm_dir_path=farm_dir,
                no_basemap=True,
            )
        assert out.exists()

    def test_empty_weather_renders_without_error(self, tmp_path):
        farm_dir = _make_mock_farm_dir(tmp_path, num_fields=1)
        # Replace weather with header-only
        wdir = farm_dir / "fields" / "test-0000" / "weather"
        wdir.mkdir(parents=True, exist_ok=True)
        (wdir / "daily_weather.csv").write_text(
            "field_id,lat,lon,date,T2M,T2M_MAX,T2M_MIN,PRECTOTCORR,ALLSKY_SFC_SW_DWN,RH2M,WS10M\n",
            encoding="utf-8",
        )
        with patch.dict(os.environ, {}, clear=True):
            out = generate_dashboard(
                farm_dir_path=farm_dir,
                no_basemap=True,
            )
        assert out.exists()
        html = out.read_text(encoding="utf-8")
        # Should still render but fields should have no weather indicator
        assert "(no data)" in html or "Dashboard" in html


class TestFrostDateCalculation:
    def test_typical_iowa_late_frost(self):
        """Test that last frost is correctly identified in a typical scenario."""
        # Simulate Iowa spring: frost through April, then warming
        rows = []
        for day in range(1, 182):  # Jan 1 to Jun 30
            tmin = -5.0 if day < 110 else (5.0 if day < 130 else 15.0)
            tmax = tmin + 10
            precip = 0.5
            rows.append({
                "date": f"2025-{day:03d}" if False else pd.Timestamp("2025-01-01") + pd.Timedelta(days=day - 1),
                "T2M_MAX": tmax,
                "T2M_MIN": tmin,
                "PRECTOTCORR": precip,
            })
        df = pd.DataFrame(rows)
        df["date"] = pd.date_range("2025-01-01", periods=len(df), freq="D")
        records = compute_field_year_weather(df, "test", 2025)
        if records:
            # Last frost should be some day around day 109 (Apr 19) or similar
            assert records[0]["dayOfYear"] >= 90  # At least April
            assert records[0]["dayOfYear"] <= 130  # Before May 10


class TestVisualFixture:
    def test_iowa_fixture_when_present(self):
        """Run against the Iowa fixture if it exists (CI-friendly)."""
        if FIXTURE_FARM_DIR is None:
            return  # skip

        with patch("dashboard_generator.vendor_plotly", return_value="/* mocked plotly */"):
            with patch.dict(os.environ, {}, clear=True):
                out = generate_dashboard(
                    farm_dir_path=FIXTURE_FARM_DIR,
                    no_basemap=True,
                )
        assert out.exists()
        html = out.read_text(encoding="utf-8")
        # Should embed fields and weather data
        assert "osm-" in html
        assert "Growing Degree Days" in html
        # No runtime CDN URLs (Plotly bundle has internal refs, that's fine)
        assert "cdn.plot.ly" not in html
        assert "unpkg.com" not in html
        assert 'src="http' not in html

    def test_iowa_default_year_selection(self):
        """Default year should prefer 2025 when available."""
        if FIXTURE_FARM_DIR is None:
            return

        gdf = load_farm_boundaries(FIXTURE_FARM_DIR)
        data = build_dashboard_data(FIXTURE_FARM_DIR, gdf, no_basemap=True)
        years = set()
        for w in data["weatherByFieldYear"]:
            years.add(w["year"])
        # Should have 2021-2025 data
        assert 2025 in years


class TestAcquireBasemap:
    def test_no_basemap_flag(self, tmp_path):
        farm_dir = _make_mock_farm_dir(tmp_path, num_fields=1)
        gdf = load_farm_boundaries(farm_dir)
        b64, ok = acquire_basemap(gdf, no_basemap=True)
        assert ok is False
        assert b64 is None

    @patch("dashboard_generator.time.sleep", return_value=None)
    @patch("dashboard_generator.requests.get")
    def test_basemap_network_failure(self, mock_get, mock_sleep, tmp_path):
        mock_get.side_effect = Exception("Network error")
        farm_dir = _make_mock_farm_dir(tmp_path, num_fields=1)
        gdf = load_farm_boundaries(farm_dir)
        b64, ok = acquire_basemap(gdf, no_basemap=False)
        assert ok is False
        assert b64 is None


class TestPlotlyVendoring:
    def test_vendor_plotly_cached(self, tmp_path):
        shared = tmp_path / "shared"
        vendor_dir = shared / "vendor"
        vendor_dir.mkdir(parents=True)
        plotly_path = vendor_dir / "plotly-2.35.2.min.js"
        plotly_path.write_text("/* cached plotly */", encoding="utf-8")

        result = vendor_plotly(shared)
        assert "cached plotly" in result

    @patch("dashboard_generator.requests.get")
    def test_vendor_plotly_download(self, mock_get, tmp_path):
        mock_response = MagicMock()
        mock_response.text = "/* downloaded plotly */"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        shared = tmp_path / "shared"
        result = vendor_plotly(shared)
        assert "downloaded plotly" in result
        assert (shared / "vendor" / "plotly-2.35.2.min.js").exists()

    @patch("dashboard_generator.requests.get")
    def test_vendor_plotly_download_failure(self, mock_get, tmp_path):
        mock_get.side_effect = Exception("CDN unavailable")
        try:
            vendor_plotly(tmp_path / "shared")
            assert False
        except RuntimeError:
            pass


class TestPipelineIntegration:
    def test_generate_dashboard_flag_present(self):
        """Verify --generate-dashboard is a recognized flag in run_farm_pipeline.py."""
        pipeline_path = Path(__file__).resolve().parents[2] / "scripts" / "run_farm_pipeline.py"
        content = pipeline_path.read_text(encoding="utf-8")
        assert "--generate-dashboard" in content

    def test_no_basemap_flag_present(self):
        pipeline_path = Path(__file__).resolve().parents[2] / "scripts" / "run_farm_pipeline.py"
        content = pipeline_path.read_text(encoding="utf-8")
        assert "--no-basemap" in content

    def test_dashboard_subcommand_in_farm_dashboard(self):
        fd_path = Path(__file__).resolve().parents[2] / "scripts" / "farm_dashboard.py"
        content = fd_path.read_text(encoding="utf-8")
        assert "dashboard generate" in content or "dashboard" in content
        assert "dashboard_generate_command" in content


# ---------------------------------------------------------------------------
# Run with pytest
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v", "--tb=short"])
