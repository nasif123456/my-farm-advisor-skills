#!/usr/bin/env python3
"""Rule-based field interpretation text generation."""

from __future__ import annotations

from typing import Any

import pandas as pd


def _find_row(df: pd.DataFrame, field_id: str) -> pd.Series | None:
    match = df[df["field_id"] == field_id]
    return match.iloc[0] if not match.empty else None


def interpret_crop_health(df: pd.DataFrame, field_id: str) -> str:
    row = _find_row(df, field_id)
    if row is None:
        return ""

    parts = []
    ndvi = row.get("ndvi_score")
    stress = row.get("crop_stress_indicator")
    ndvi_corn = row.get("ndvi_corn")
    ndvi_soy = row.get("ndvi_soybean")

    has_ndvi = ndvi is not None and not (isinstance(ndvi, float) and pd.isna(ndvi))

    if has_ndvi:
        if isinstance(stress, (int, float)) and not pd.isna(stress):
            if stress <= 30:
                parts.append(f"Field {field_id} has low apparent crop stress (indicator: {stress:.0f}/100).")
            elif stress <= 60:
                parts.append(f"Field {field_id} shows moderate apparent stress (indicator: {stress:.0f}/100).")
            else:
                parts.append(f"Field {field_id} has high apparent stress (indicator: {stress:.0f}/100), "
                            "suggesting possible crop health concerns.")

        if ndvi_corn is not None and not pd.isna(ndvi_corn):
            parts.append(f"Corn mean NDVI: {ndvi_corn:.3f}.")
        if ndvi_soy is not None and not pd.isna(ndvi_soy):
            parts.append(f"Soybean mean NDVI: {ndvi_soy:.3f}.")

    return " ".join(parts)


def interpret_soil(df: pd.DataFrame, field_id: str) -> str:
    row = _find_row(df, field_id)
    if row is None:
        return ""

    parts = []
    shs = row.get("soil_health_score")
    om = row.get("organic_matter_pct")
    ph = row.get("soil_ph")
    drainage = row.get("drainage_class")

    if shs is not None and not (isinstance(shs, float) and pd.isna(shs)):
        if shs >= 80:
            parts.append(f"Soil condition is strong (score: {shs:.0f}/100).")
        elif shs >= 60:
            parts.append(f"Soil condition is generally suitable (score: {shs:.0f}/100).")
        elif shs >= 40:
            parts.append(f"Soil shows moderate limitations (score: {shs:.0f}/100).")
        else:
            parts.append(f"Soil is a higher management priority (score: {shs:.0f}/100).")

    if om is not None and not (isinstance(om, float) and pd.isna(om)):
        parts.append(f"Organic matter: {om:.1f}%.")
    if ph is not None and not (isinstance(ph, float) and pd.isna(ph)):
        parts.append(f"pH: {ph:.1f}.")
    if drainage is not None and not (isinstance(drainage, str) and pd.isna(drainage)):
        parts.append(f"Drainage class: {drainage}.")

    return " ".join(parts)


def interpret_weather(df: pd.DataFrame, field_id: str) -> str:
    row = _find_row(df, field_id)
    if row is None:
        return ""

    parts = []
    ws = row.get("weather_suitability_score")
    precip = row.get("total_precipitation_mm")
    gdd = row.get("cumulative_gdd")
    dry_days = row.get("dry_day_count")

    if ws is not None and not (isinstance(ws, float) and pd.isna(ws)):
        if ws >= 80:
            parts.append("Weather conditions were favourable.")
        elif ws >= 60:
            parts.append("Weather conditions were moderately suitable.")
        else:
            parts.append("Weather conditions were less favourable compared to other fields.")

    if precip is not None and not (isinstance(precip, float) and pd.isna(precip)):
        parts.append(f"Seasonal rainfall: {precip:.0f} mm.")
    if gdd is not None and not (isinstance(gdd, float) and pd.isna(gdd)):
        parts.append(f"Cumulative GDD: {gdd:.0f} (base 10C).")
    if dry_days is not None and not (isinstance(dry_days, float) and pd.isna(dry_days)):
        parts.append(f"Dry days: {int(dry_days)}.")

    return " ".join(parts)


def generate_field_summary(df: pd.DataFrame, field_id: str) -> dict[str, str]:
    return {
        "crop_health": interpret_crop_health(df, field_id),
        "soil": interpret_soil(df, field_id),
        "weather": interpret_weather(df, field_id),
    }


def generate_grower_summary(df: pd.DataFrame) -> str:
    if df.empty:
        return "No field data available for this grower."

    parts = []
    n = len(df)
    total_acres = df["area_acres"].sum() if "area_acres" in df.columns else 0

    parts.append(f"This grower has {n} field(s) totalling {total_acres:.0f} acres.")

    if "field_intelligence_score" in df.columns:
        scores = df["field_intelligence_score"].dropna()
        if not scores.empty:
            mean_fis = scores.mean()
            parts.append(f"The mean Field Intelligence Score is {mean_fis:.0f}/100.")

            top = df.loc[scores.idxmax()]
            bottom = df.loc[scores.idxmin()]
            parts.append(
                f"The highest-scoring field is {top['field_id']} ({scores.max():.0f}/100). "
                f"The lowest-scoring field is {bottom['field_id']} ({scores.min():.0f}/100)."
            )

    if "crop_stress_indicator" in df.columns:
        high_stress = df[df["crop_stress_indicator"] > 60]
        if not high_stress.empty:
            ids = ", ".join(high_stress["field_id"].tolist())
            parts.append(f"Fields with high apparent stress: {ids}.")

    if "conservation_priority_score" in df.columns:
        high_priority = df[df["conservation_priority_score"] > 60]
        if not high_priority.empty:
            ids = ", ".join(high_priority["field_id"].tolist())
            parts.append(f"Fields flagged as conservation priority: {ids}.")

    parts.append(
        "These results support field prioritisation. "
        "They do not replace field scouting or professional agronomic judgement."
    )

    return " ".join(parts)


def generate_key_findings(df: pd.DataFrame) -> list[str]:
    findings = []
    if df.empty:
        return findings

    if "field_intelligence_score" in df.columns:
        scores = df["field_intelligence_score"].dropna()
        if not scores.empty:
            worst = df.loc[scores.idxmin()]
            findings.append(
                f"Field {worst['field_id']} has the lowest Field Intelligence Score "
                f"({scores.min():.0f}/100) and should be prioritised for inspection."
            )

    if "crop_stress_indicator" in df.columns:
        high_stress = df[df["crop_stress_indicator"] > 60]
        for _, r in high_stress.iterrows():
            findings.append(
                f"Field {r['field_id']} has high apparent crop stress "
                f"({r['crop_stress_indicator']:.0f}/100). Consider scouting."
            )

    if "soil_health_score" in df.columns:
        low_soil = df[df["soil_health_score"] < 40]
        for _, r in low_soil.iterrows():
            findings.append(
                f"Field {r['field_id']} has a low soil health score "
                f"({r['soil_health_score']:.0f}/100). Soil testing may be warranted."
            )

    if "conservation_priority_score" in df.columns:
        top_cons = df.nlargest(2, "conservation_priority_score")
        for _, r in top_cons.iterrows():
            findings.append(
                f"Field {r['field_id']} has a high conservation priority score "
                f"({r['conservation_priority_score']:.0f}/100), suggesting possible "
                f"drainage, erosion, or nutrient management needs."
            )

    if not findings:
        findings.append("No fields require immediate priority attention based on available data.")

    return findings
