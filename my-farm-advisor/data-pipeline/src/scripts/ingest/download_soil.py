#!/usr/bin/env python3
# ruff: noqa: E402,I001
"""Download SSURGO soil data into canonical grower paths."""

import os
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

_SCRIPTS_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SCRIPTS_DIR))
sys.path.insert(0, str(_SCRIPTS_DIR / "lib"))

from paths import (  # pyright: ignore[reportMissingImports]
    farm_boundary_path,
    farm_manifest_dir,
    farm_soil_sample_path,
    farm_ssurgo_full_path,
    farm_ssurgo_summary_path,
    field_soil_full_path,
    field_soil_polygon_path,
    field_soil_summary_path,
)
from reporting_bootstrap import (
    ensure_canonical_data_tree,
    ensure_skill_path,
    field_slug_map_from_inventory,
)

ensure_skill_path("ssurgo-soil")

from ssurgo_soil import download_soil  # pyright: ignore[reportMissingImports]
from ssurgo_workflows import query_mupolygons_for_field  # pyright: ignore[reportMissingImports]

SDA_URL = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"


def _query_sda(sql: str, timeout: int = 120) -> list[list[object]]:
    response = requests.post(SDA_URL, data={"query": sql, "format": "JSON"}, timeout=timeout)
    response.raise_for_status()
    return response.json().get("Table", [])


def _fallback_field_soil(fields: gpd.GeoDataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    fields_wgs84 = fields.to_crs(epsg=4326)

    for _, field in fields_wgs84.iterrows():
        field_id = str(field["field_id"])
        field_wkt = field.geometry.wkt
        mukey_sql = f"""
        SELECT DISTINCT m.mukey
        FROM mupolygon m
        WHERE m.mupolygonkey IN (
            SELECT * FROM SDA_Get_Mupolygonkey_from_intersection_with_WktWgs84('{field_wkt}')
        )
        """
        try:
            mukey_rows = _query_sda(mukey_sql, timeout=60)
        except Exception:
            mukey_rows = []
        mukeys = [str(row[0]) for row in mukey_rows if row and row[0] is not None]
        if not mukeys:
            continue

        attr_sql = f"""
        SELECT mu.muname, c.mukey, c.compname, c.comppct_r, c.drainagecl,
               ch.hzdept_r, ch.hzdepb_r, ch.om_r, ch.ph1to1h2o_r,
               ch.awc_r, ch.claytotal_r, ch.sandtotal_r, ch.silttotal_r,
               ch.dbthirdbar_r, ch.cec7_r, ch.kwfact
        FROM mapunit mu
        INNER JOIN component c ON mu.mukey = c.mukey
        LEFT JOIN chorizon ch ON c.cokey = ch.cokey
        WHERE c.mukey IN ({", ".join(repr(m) for m in mukeys)})
          AND c.majcompflag = 'Yes'
          AND (ch.hzdept_r < 100 OR ch.hzdept_r IS NULL)
        ORDER BY c.mukey, c.comppct_r DESC, ch.hzdept_r ASC
        """
        try:
            attr_rows = _query_sda(attr_sql)
        except Exception:
            attr_rows = []

        for row in attr_rows:
            if len(row) < 16:
                continue
            rows.append(
                {
                    "field_id": field_id,
                    "muname": row[0],
                    "mukey": str(row[1]),
                    "compname": row[2],
                    "comppct_r": row[3],
                    "drainagecl": row[4],
                    "hzdept_r": row[5],
                    "hzdepb_r": row[6],
                    "om_r": row[7],
                    "ph1to1h2o_r": row[8],
                    "awc_r": row[9],
                    "claytotal_r": row[10],
                    "sandtotal_r": row[11],
                    "silttotal_r": row[12],
                    "dbthirdbar_r": row[13],
                    "cec7_r": row[14],
                    "kwfact": row[15],
                }
            )

    df = pd.DataFrame(rows)
    if df.empty:
        return df

    for col in [
        "comppct_r",
        "hzdept_r",
        "hzdepb_r",
        "om_r",
        "ph1to1h2o_r",
        "awc_r",
        "claytotal_r",
        "sandtotal_r",
        "silttotal_r",
        "dbthirdbar_r",
        "cec7_r",
        "kwfact",
    ]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _write_summary(soil_data: pd.DataFrame) -> None:
    if soil_data.empty:
        return
    raise RuntimeError("_write_summary requires an explicit output path")


def _soil_polygon_cache_has_features(path: Path) -> bool:
    if not path.exists():
        return False
    try:
        polygons = gpd.read_file(path)
    except Exception:
        return False
    return not polygons.empty


def _write_field_polygon_caches(
    *,
    fields: gpd.GeoDataFrame,
    soil_data: pd.DataFrame,
    field_slug_map: dict[str, str],
    grower_slug: str,
    farm_slug: str,
    force: bool,
) -> None:
    if soil_data.empty or not field_slug_map:
        return

    fields_wgs84 = fields.to_crs(epsg=4326)
    soil_rows = soil_data.copy()
    if "mukey" not in soil_rows.columns:
        return
    soil_rows["mukey"] = soil_rows["mukey"].astype(str)

    for _, field in fields_wgs84.iterrows():
        field_id = str(field.get("field_id", "")).strip()
        field_slug = field_slug_map.get(field_id)
        if not field_slug:
            continue
        cache_path = field_soil_polygon_path(grower_slug, farm_slug, field_slug)
        if not force and _soil_polygon_cache_has_features(cache_path):
            continue
        mukeys = sorted(
            soil_rows.loc[soil_rows["field_id"].astype(str) == field_id, "mukey"].dropna().unique()
        )
        if not mukeys:
            continue
        polygons = query_mupolygons_for_field(field.geometry.wkt, mukeys)
        if polygons.empty:
            continue
        polygons["field_id"] = field_id
        field_boundary = gpd.GeoDataFrame(
            fields_wgs84[fields_wgs84["field_id"].astype(str) == field_id].copy(),
            geometry="geometry",
            crs=fields_wgs84.crs,
        )
        try:
            polygons = gpd.overlay(
                polygons,
                field_boundary,
                how="intersection",
            )
        except Exception:
            pass
        if polygons.empty:
            continue
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        polygons.to_file(cache_path, driver="GeoJSON")


def _compute_weighted_soil_summary(soil_data: pd.DataFrame) -> pd.DataFrame:
    """Field-level weighted summary from SSURGO component x horizon data.

    Depth separation:
      - OM, pH, CEC, clay, sand: surface interval (0-30 cm) weighted by
        comppct_r and the horizon overlap with 0-30 cm.
      - AWC: full profile (0-100 cm) — computed per-map-unit then averaged
        across map units to avoid double-counting.

    Drainage, dominant soil, and map-unit name come from the highest-weight
    (comppct_r x surface or full thickness) component-horizon row.
    """
    df = soil_data.copy()
    df["hzthick_cm"] = (df["hzdepb_r"] - df["hzdept_r"]).clip(lower=0)
    df["hzthick_in"] = df["hzthick_cm"] / 2.54
    comppct = df["comppct_r"].fillna(0)

    # ── Surface (0-30 cm) overlap weight for OM, pH, CEC, clay, sand ──
    sfc_top = df["hzdept_r"].clip(lower=0)
    sfc_bot = df["hzdepb_r"].clip(upper=30)
    df["_sfc_overlap"] = (sfc_bot - sfc_top).clip(lower=0)
    df["_w_sfc"] = comppct * df["_sfc_overlap"]

    # ── Full profile weight for AWC ──
    df["_w_full"] = comppct * df["hzthick_cm"]

    def _w_mean(s: pd.Series, w: pd.Series) -> float:
        mask = s.notna() & w.notna() & (w > 0)
        if not mask.any():
            return float("nan")
        return float((s[mask] * w[mask]).sum() / w[mask].sum())

    def _erosion_risk(kw_mean: float) -> str | None:
        if pd.isna(kw_mean):
            return None
        if kw_mean >= 0.40:
            return "high"
        if kw_mean >= 0.25:
            return "moderate"
        return "low"

    records = []
    for field_id, grp in df.groupby("field_id"):
        w_sfc = grp["_w_sfc"]
        best_idx = w_sfc.idxmax() if (w_sfc > 0).any() else grp.index[0]
        best_row = grp.loc[best_idx]

        # Map unit count and sets
        mukeys_in_field = grp["mukey"].unique()
        n_mu = len(mukeys_in_field)

        # AWC: compute per-mukey, then average across map units
        aws_per_mukey = []
        for _, mu in grp.groupby("mukey"):
            mu_pct = mu["comppct_r"].fillna(0)
            mu_aws = (mu["awc_r"] * mu["hzthick_in"] * mu_pct / 100.0).sum()
            aws_per_mukey.append(mu_aws)
        field_aws = sum(aws_per_mukey) / n_mu if aws_per_mukey else 0.0

        kw_vals = pd.to_numeric(grp["kwfact"], errors="coerce")
        kw_mean = _w_mean(kw_vals, w_sfc) if not kw_vals.isna().all() else float("nan")

        # Dominant map-unit percentage (comppct_r of the highest-weight component)
        dom_comppct = float(best_row["comppct_r"]) if pd.notna(best_row.get("comppct_r")) else None

        # Erosion evidence
        erisk = _erosion_risk(kw_mean)
        if erisk is not None:
            er_source = f"SSURGO K-factor (kwfact) — soil erodibility only, not full RUSLE (mean K={kw_mean:.4f})"
        else:
            er_source = None

        records.append(
            {
                "field_id": field_id,
                "n_mukeys": n_mu,
                "n_components": int(grp["compname"].nunique()),
                "n_horizons": len(grp),
                "avg_om_pct": _w_mean(grp["om_r"], w_sfc),
                "avg_ph": _w_mean(grp["ph1to1h2o_r"], w_sfc),
                "total_aws_inches": round(field_aws, 4),
                "avg_cec": _w_mean(grp["cec7_r"], w_sfc),
                "avg_clay_pct": _w_mean(grp["claytotal_r"], w_sfc),
                "avg_sand_pct": _w_mean(grp["sandtotal_r"], w_sfc),
                "dominant_soil": str(best_row["compname"]),
                "dominant_mapunit_name": str(best_row.get("muname", "")),
                "dominant_mapunit_pct": dom_comppct,
                "drainage_class": str(best_row["drainagecl"]),
                "ph_constraint": None,
                "erosion_risk": erisk,
                "k_factor": round(kw_mean, 4) if not pd.isna(kw_mean) else None,
                "erosion_evidence_source": er_source,
                "om_depth_cm": 30,
                "ph_depth_cm": 30,
                "cec_depth_cm": 30,
                "awc_profile_depth_cm": 100,
                "ssurgo_coverage_pct": None,
                "unmapped_area_pct": None,
                "soil_aggregation_method": (
                    "component-percentage weighted (comppct_r x horizon overlap with target depth); "
                    "OM/pH/CEC/clay/sand: surface 0-30 cm; AWC: root-zone 0-100 cm per-map-unit then averaged"
                ),
            }
        )

    return pd.DataFrame(records)


def main():
    print("=" * 60)
    print("Step 2: Download SSURGO Soil Data")
    print("=" * 60)

    grower_slug = os.environ.get("AG_GROWER_SLUG", "default-grower")
    farm_slug = os.environ.get("AG_FARM_SLUG", "default-farm")
    default_inventory = farm_manifest_dir(grower_slug, farm_slug) / "field-inventory.csv"
    inventory_path = Path(os.environ.get("AG_INVENTORY_CSV", str(default_inventory)))
    ensure_canonical_data_tree(
        grower_slug=grower_slug, farm_slug=farm_slug, inventory_path=inventory_path
    )
    field_slug_map = field_slug_map_from_inventory(
        inventory_path if inventory_path.exists() else None
    )

    boundaries_path = farm_boundary_path(grower_slug, farm_slug)
    fields = gpd.read_file(boundaries_path)
    print(f"Loaded {len(fields)} fields")

    farm_full_output = farm_ssurgo_full_path(grower_slug, farm_slug)
    farm_summary_output = farm_ssurgo_summary_path(grower_slug, farm_slug)
    farm_sample_output = farm_soil_sample_path(grower_slug, farm_slug)
    farm_sample_output.parent.mkdir(parents=True, exist_ok=True)
    force = os.environ.get("AG_FORCE") == "1"

    if (
        farm_full_output.exists()
        and farm_summary_output.exists()
        and farm_sample_output.exists()
        and not force
    ):
        soil_data = pd.read_csv(farm_sample_output)
        grouped = pd.read_csv(farm_summary_output)
        expected_field_ids = set(fields["field_id"].astype(str).tolist())
        cached_field_ids = (
            set(soil_data["field_id"].astype(str).tolist())
            if "field_id" in soil_data.columns
            else set()
        )
        missing_field_ids = sorted(expected_field_ids - cached_field_ids)
        if missing_field_ids:
            print(
                "  Cached SSURGO rows are missing field IDs; refreshing soil tables for: "
                + ", ".join(missing_field_ids)
            )
        else:
            if field_slug_map:
                for field_id, field_slug in field_slug_map.items():
                    field_rows = soil_data[
                        soil_data["field_id"].astype(str) == str(field_id)
                    ].copy()
                    if not field_rows.empty:
                        full_target = field_soil_full_path(grower_slug, farm_slug, field_slug)
                        full_target.parent.mkdir(parents=True, exist_ok=True)
                        field_rows.to_csv(full_target, index=False)
                    summary_rows = grouped[grouped["field_id"].astype(str) == str(field_id)].copy()
                    if not summary_rows.empty:
                        summary_target = field_soil_summary_path(grower_slug, farm_slug, field_slug)
                        summary_target.parent.mkdir(parents=True, exist_ok=True)
                        summary_rows.to_csv(summary_target, index=False)
                _write_field_polygon_caches(
                    fields=fields,
                    soil_data=soil_data,
                    field_slug_map=field_slug_map,
                    grower_slug=grower_slug,
                    farm_slug=farm_slug,
                    force=False,
                )
            print(f"skip  SSURGO API fetch (cached): {farm_sample_output}")
            return soil_data

    soil_data = download_soil(
        fields,
        field_id_column="field_id",
        max_depth_cm=100,
        output_path=str(farm_sample_output),
    )

    if soil_data.empty:
        print("  Primary SSURGO download returned no rows; querying SDA fallback summaries...")
        soil_data = _fallback_field_soil(fields)
    if not soil_data.empty:
        soil_data.to_csv(farm_full_output, index=False)
        soil_data.to_csv(farm_sample_output, index=False)
        grouped = _compute_weighted_soil_summary(soil_data)
        grouped.to_csv(farm_summary_output, index=False)

        if field_slug_map:
            for field_id, field_slug in field_slug_map.items():
                field_rows = soil_data[soil_data["field_id"].astype(str) == str(field_id)].copy()
                if not field_rows.empty:
                    full_target = field_soil_full_path(grower_slug, farm_slug, field_slug)
                    full_target.parent.mkdir(parents=True, exist_ok=True)
                    field_rows.to_csv(full_target, index=False)
                summary_rows = grouped[grouped["field_id"].astype(str) == str(field_id)].copy()
                if not summary_rows.empty:
                    summary_target = field_soil_summary_path(grower_slug, farm_slug, field_slug)
                    summary_target.parent.mkdir(parents=True, exist_ok=True)
                    summary_rows.to_csv(summary_target, index=False)

            _write_field_polygon_caches(
                fields=fields,
                soil_data=soil_data,
                field_slug_map=field_slug_map,
                grower_slug=grower_slug,
                farm_slug=farm_slug,
                force=force,
            )

    print(
        f"\n✓ Downloaded {len(soil_data)} soil records for {soil_data['field_id'].nunique()} fields"
    )
    print(f"  Output: {farm_sample_output}")

    return soil_data


if __name__ == "__main__":
    main()
