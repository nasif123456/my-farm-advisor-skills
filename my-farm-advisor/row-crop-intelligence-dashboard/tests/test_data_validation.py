#!/usr/bin/env python3
"""Tests for data validation."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.validate_inputs import validate_field_boundaries, ValidationResult


def make_valid_boundary(tmp_path: Path) -> Path:
    gdf = gpd.GeoDataFrame({
        "field_id": ["f1", "f2"],
        "area_acres": [100, 200],
        "geometry": [
            Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
            Polygon([(2, 2), (3, 2), (3, 3), (2, 3)]),
        ],
    }, crs="EPSG:4326")
    path = tmp_path / "field_boundaries.geojson"
    gdf.to_file(path, driver="GeoJSON")
    return path


def test_valid_boundaries():
    with tempfile.TemporaryDirectory() as td:
        path = make_valid_boundary(Path(td))
        result = validate_field_boundaries(path)
        assert result.passed
        assert result.field_count == 2


def test_missing_boundary():
    result = validate_field_boundaries(Path("/nonexistent.geojson"))
    assert not result.passed


def test_missing_field_id():
    with tempfile.TemporaryDirectory() as td:
        gdf = gpd.GeoDataFrame({
            "name": ["f1"],
            "geometry": [Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])],
        }, crs="EPSG:4326")
        path = Path(td) / "no_field_id.geojson"
        gdf.to_file(path, driver="GeoJSON")
        result = validate_field_boundaries(path)
        assert not result.passed


def test_duplicate_field_ids():
    with tempfile.TemporaryDirectory() as td:
        gdf = gpd.GeoDataFrame({
            "field_id": ["f1", "f1"],
            "geometry": [
                Polygon([(0, 0), (1, 0), (1, 1), (0, 1)]),
                Polygon([(2, 2), (3, 2), (3, 3), (2, 3)]),
            ],
        }, crs="EPSG:4326")
        path = Path(td) / "dupes.geojson"
        gdf.to_file(path, driver="GeoJSON")
        result = validate_field_boundaries(path)
        assert not result.passed


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__]))
