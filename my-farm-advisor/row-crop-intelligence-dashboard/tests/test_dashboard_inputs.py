#!/usr/bin/env python3
"""Tests for dashboard input validation."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.data_loader import load_dashboard_data, validate_dashboard_data, DashboardData


def test_empty_dashboard_data():
    d = DashboardData()
    warnings = validate_dashboard_data(d)
    assert any("empty" in w.lower() for w in warnings)


def test_validate_missing_boundaries():
    d = DashboardData()
    d.field_summary = pd.DataFrame({"field_id": ["f1"], "ndvi_score": [50]})
    warnings = validate_dashboard_data(d)
    boundary_warnings = [w for w in warnings if "boundar" in w.lower()]
    assert len(boundary_warnings) > 0


def test_validate_high_missing():
    d = DashboardData()
    df = pd.DataFrame({
        "field_id": ["f1", "f2"],
        "ndvi_score": [50, None],
        "organic_matter_pct": [None, None],
    })
    d.field_summary = df
    warnings = validate_dashboard_data(d)
    om_warnings = [w for w in warnings if "organic_matter_pct" in w]
    assert len(om_warnings) > 0


def test_load_and_save(tmp_path: Path | None = None):
    if tmp_path is None:
        tmp_path = Path(tempfile.mkdtemp())

    df = pd.DataFrame({
        "field_id": ["f1"],
        "area_acres": [100],
        "ndvi_score": [50],
        "field_intelligence_score": [60.5],
        "risk_category": ["Moderate"],
    })
    df.to_csv(tmp_path / "field_summary.csv", index=False)

    meta = {"grower_id": "test", "year": 2024, "field_count": 1}
    with open(tmp_path / "dashboard_metadata.json", "w") as f:
        json.dump(meta, f, indent=2)

    d = load_dashboard_data(str(tmp_path))
    assert not d.field_summary.empty
    assert d.metadata["grower_id"] == "test"
    assert d.field_summary["field_intelligence_score"].iloc[0] == 60.5


def test_missing_data_dir():
    d = load_dashboard_data("/tmp/nonexistent_path_xyz")
    assert d.field_summary.empty


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__]))
