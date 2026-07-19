#!/usr/bin/env python3
"""Rule-based field interpretation text generation.

All interpretations are derived directly from the processed data.
Generated text:
- Uses "apparent stress" (not "crop stress") because remote sensing
  cannot diagnose the exact cause.
- Distinguishes measured values from composite scores.
- Identifies the specific evidence behind each recommendation.
- Does not claim causation or diagnose crop problems.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


def _find_row(df: pd.DataFrame, field_id: str) -> pd.Series | None:
    match = df[df["field_id"] == field_id]
    return match.iloc[0] if not match.empty else None


def _safe(val: Any) -> bool:
    """Check if a value is not None and not NaN."""
    if val is None:
        return False
    if isinstance(val, float):
        return not pd.isna(val)
    if isinstance(val, str):
        return val.strip() != "" and val.strip().lower() != "nan"
    return True


def _confidence_note(row: pd.Series) -> str:
    """Return a data-sufficiency note based on the row's confidence fields."""
    parts = []
    confidence = row.get("fis_confidence", "")
    if confidence and _safe(confidence):
        parts.append(f"Analytical confidence: {confidence}.")
    obs = row.get("valid_ndvi_observations")
    if obs is not None and _safe(obs) and obs > 0:
        parts.append(f"{int(obs)} valid NDVI observation(s) available.")
    return " ".join(parts)


def interpret_crop_health(df: pd.DataFrame, field_id: str) -> str:
    row = _find_row(df, field_id)
    if row is None:
        return ""

    parts = []
    ndvi_cond_score = row.get("ndvi_condition_score")
    stress = row.get("crop_stress_apparent")
    mean_ndvi = row.get("mean_ndvi")
    crop_name = row.get("crop_name", "Unknown")
    ndvi_confidence = row.get("ndvi_condition_confidence", "")

    has_ndvi = _safe(ndvi_cond_score)

    if has_ndvi:
        # Condition score narrative
        if _safe(ndvi_cond_score):
            if ndvi_cond_score >= 75:
                parts.append(f"NDVI Condition Score: {ndvi_cond_score:.0f}/100 — strong vegetation greenness.")
            elif ndvi_cond_score >= 50:
                parts.append(f"NDVI Condition Score: {ndvi_cond_score:.0f}/100 — moderate vegetation greenness.")
            elif ndvi_cond_score >= 30:
                parts.append(f"NDVI Condition Score: {ndvi_cond_score:.0f}/100 — below-average vegetation greenness.")
            else:
                parts.append(f"NDVI Condition Score: {ndvi_cond_score:.0f}/100 — low vegetation greenness.")

        # Raw NDVI
        if _safe(mean_ndvi):
            parts.append(f"Mean NDVI: {mean_ndvi:.3f} (raw satellite measurement, range -1 to 1).")

        # Crop type
        if _safe(crop_name) and crop_name != "Unknown":
            parts.append(f"Crop type: {crop_name}.")

        # Apparent stress (using crop_stress_apparent)
        if _safe(stress):
            if stress <= 30:
                parts.append(f"Apparent stress indicator: {stress:.0f}/100 — low.")
            elif stress <= 60:
                parts.append(f"Apparent stress indicator: {stress:.0f}/100 — moderate.")
            else:
                parts.append(f"Apparent stress indicator: {stress:.0f}/100 — elevated. Field scouting is recommended to investigate possible causes.")

        # Confidence
        if _safe(ndvi_confidence):
            parts.append(f"NDVI confidence: {ndvi_confidence}.")

    else:
        parts.append(f"NDVI data is not available for field {field_id}.")

    return " ".join(parts)


def interpret_soil(df: pd.DataFrame, field_id: str) -> str:
    row = _find_row(df, field_id)
    if row is None:
        return ""

    parts = []
    scss = row.get("soil_condition_screening_score")
    category = row.get("soil_condition_screening_category", "")
    om = row.get("organic_matter_pct")
    ph = row.get("soil_ph")
    drainage = row.get("drainage_class")
    erosion = row.get("erosion_risk")
    om_score = row.get("om_score")
    ph_score = row.get("ph_suitability_score")
    drainage_score = row.get("drainage_score")
    awc_score = row.get("awc_score")
    cec_score = row.get("cec_score")
    soil_conf = row.get("soil_score_confidence", "")
    erosion_source = row.get("erosion_evidence_source", "")

    if _safe(scss):
        label = f" — {category}" if _safe(category) and category != "nan" else ""
        if scss >= 75:
            parts.append(f"Soil Condition Screening Score: {scss:.0f}/100{label} — generally favourable profile.")
        elif scss >= 50:
            parts.append(f"Soil Condition Screening Score: {scss:.0f}/100{label} — moderate limitations.")
        else:
            parts.append(f"Soil Condition Screening Score: {scss:.0f}/100{label} — higher management priority.")

    if _safe(soil_conf):
        parts.append(f"Soil-data confidence: {soil_conf}.")

    # Depth context
    depth_parts = []
    om_depth = row.get("om_depth_cm")
    ph_depth = row.get("ph_depth_cm")
    cec_depth = row.get("cec_depth_cm")
    awc_depth = row.get("awc_profile_depth_cm")
    if _safe(om_depth):
        depth_parts.append(f"OM/pH/CEC measured at 0-{int(om_depth)} cm surface depth")
    if _safe(awc_depth):
        depth_parts.append(f"AWC measured across 0-{int(awc_depth)} cm root zone")
    if depth_parts:
        parts.append("Depth context: " + "; ".join(depth_parts) + ".")

    # Component-specific observations — evidence-based, no overstatement
    component_obs = []
    if _safe(om):
        if _safe(om_score) and om_score is not None:
            if om_score < 20:
                component_obs.append(f"relatively low organic matter ({om:.2f}%)")
            elif om_score < 50:
                component_obs.append(f"moderate organic matter ({om:.2f}%)")
            elif om_score >= 80:
                component_obs.append(f"favourable organic matter ({om:.2f}%)")

    if _safe(ph):
        if _safe(ph_score) and ph_score is not None:
            if ph_score < 50:
                component_obs.append(f"pH ({ph:.2f}) is outside the general row-crop target range (6.0–7.0)")
            elif ph_score < 100:
                component_obs.append(f"pH ({ph:.2f}) is moderately suitable")
            else:
                component_obs.append(f"pH ({ph:.2f}) is within the target range")

    if _safe(drainage) and _safe(drainage_score) and drainage_score is not None:
        if drainage_score < 50:
            component_obs.append(f"drainage class '{drainage}' may limit field operations or crop growth")
        elif drainage_score < 70:
            component_obs.append(f"drainage class '{drainage}' — moderate limitation")
        elif drainage_score >= 80:
            pass

    if _safe(awc_score) and awc_score is not None:
        if awc_score < 30:
            component_obs.append("low available water capacity — drought risk is higher")
        elif awc_score < 50:
            component_obs.append("moderate available water capacity")
        elif awc_score >= 80:
            component_obs.append("favourable available water capacity")

    if _safe(cec_score) and cec_score is not None:
        if cec_score < 30:
            component_obs.append("low CEC — limited nutrient retention capacity")
        elif cec_score < 50:
            component_obs.append("moderate CEC")
        elif cec_score >= 80:
            component_obs.append("favourable CEC — good nutrient retention")

    if component_obs:
        parts.append("Notable soil characteristics: " + "; ".join(component_obs) + ".")

    # Evidence-based considerations (only when supporting data exists)
    rec_evidence = []
    if _safe(drainage) and str(drainage).strip().lower() in (
        "poorly drained", "very poorly drained", "somewhat poorly drained"
    ):
        rec_evidence.append(f"drainage class '{drainage}' may warrant inspection for ponding")
    if _safe(om) and om is not None and om < 2.0:
        rec_evidence.append(f"organic matter ({om:.2f}%) is low — consider cover cropping or reduced tillage")
    if _safe(erosion) and str(erosion).strip().lower() in ("high", "severe"):
        rec_evidence.append(f"erosion risk is '{erosion}' — consider conservation buffers or residue management")
        if _safe(erosion_source):
            rec_evidence.append(f"Erosion evidence: {erosion_source}")

    if rec_evidence:
        parts.append("Consider reviewing: " + "; ".join(rec_evidence) + ".")

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
    max_temp = row.get("max_temp_c")

    if _safe(ws):
        if ws >= 75:
            parts.append("Weather suitability score: favourable compared to other fields.")
        elif ws >= 50:
            parts.append("Weather suitability score: moderate compared to other fields.")
        else:
            parts.append("Weather suitability score: less favourable compared to other fields.")

    if _safe(precip):
        parts.append(f"Seasonal rainfall: {precip:.0f} mm.")
    if _safe(gdd):
        parts.append(f"Cumulative GDD: {gdd:.0f} (base 10°C).")
    if _safe(dry_days):
        parts.append(f"Consecutive dry days: {int(dry_days)}.")
    if _safe(max_temp):
        parts.append(f"Maximum temperature: {max_temp:.1f}°C.")

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

    parts.append(f"This grower has {n} {'field' if n == 1 else 'fields'} totalling {total_acres:.0f} acres.")

    # Row-crop only FIS stats
    if "fis_score" in df.columns and "row_crop_fis_score" in df.columns:
        rc_df = df[df["row_crop_fis_score"].notna()]
        ref_df = df[df["row_crop_fis_score"].isna()]
        rc_n = len(rc_df)
        ref_n = len(ref_df)

        # Row-crop FIS summary
        rc_scores = rc_df["fis_score"].dropna()
        if not rc_scores.empty and rc_n > 0:
            rc_mean = rc_scores.mean()
            parts.append(
                f"Of these, {rc_n} {'row-crop field has' if rc_n == 1 else 'row-crop fields have'} a mean Field Intelligence Score "
                f"of {rc_mean:.0f}/100 "
                f"(range {rc_scores.min():.0f}–{rc_scores.max():.0f})."
            )
            top_idx = rc_scores.idxmax()
            bottom_idx = rc_scores.idxmin()
            top_field = rc_df.loc[top_idx, "field_id"]
            bottom_field = rc_df.loc[bottom_idx, "field_id"]
            parts.append(
                f"Highest-scoring row-crop field: {top_field} ({rc_scores.max():.0f}/100). "
                f"Lowest-scoring row-crop field: {bottom_field} ({rc_scores.min():.0f}/100)."
            )

        if ref_n > 0:
            ref_scores = ref_df["fis_score"].dropna()
            ref_fis_note = ""
            if not ref_scores.empty:
                ref_fis_note = f" Mean FIS for reference areas: {ref_scores.mean():.0f}/100."
            parts.append(
                f"There {'is' if ref_n == 1 else 'are'} {ref_n} {'reference land-cover field' if ref_n == 1 else 'reference land-cover fields'} "
                f"(not row-crop).{ref_fis_note}"
            )

    if "crop_stress_apparent" in df.columns:
        high_stress = df[df["crop_stress_apparent"] > 60]
        if not high_stress.empty:
            ids = ", ".join(high_stress["field_id"].tolist())
            parts.append(f"Fields with elevated apparent stress: {ids}.")

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

    if "fis_score" in df.columns:
        scores = df["fis_score"].dropna()
        if not scores.empty:
            worst = df.loc[scores.idxmin()]
            best = df.loc[scores.idxmax()]
            mean_fis = scores.mean()
            findings.append(
                f"The mean Field Intelligence Score is {mean_fis:.0f}/100 "
                f"(range {scores.min():.0f}–{scores.max():.0f}). "
                f"Field {worst['field_id']} ({scores.min():.0f}) and "
                f"field {best['field_id']} ({scores.max():.0f}) bracket the range."
            )

    if "crop_stress_apparent" in df.columns:
        high_stress = df[df["crop_stress_apparent"] > 60]
        if len(high_stress) > 3:
            ids = ", ".join(high_stress["field_id"].tolist())
            findings.append(
                f"{len(high_stress)} of {len(df)} fields show elevated apparent stress "
                f"(range {high_stress['crop_stress_apparent'].min():.0f}–"
                f"{high_stress['crop_stress_apparent'].max():.0f}/100). "
                f"Affected: {ids}."
            )
        elif len(high_stress) > 0:
            for _, r in high_stress.iterrows():
                findings.append(
                    f"Field {r['field_id']} has elevated apparent stress "
                    f"({r['crop_stress_apparent']:.0f}/100). "
                    f"Field scouting is recommended to investigate."
                )

    if "soil_condition_screening_score" in df.columns:
        low_soil = df[df["soil_condition_screening_score"] < 40]
        if len(low_soil) > 2:
            ids = ", ".join(low_soil["field_id"].tolist())
            findings.append(
                f"{len(low_soil)} fields have soil screening scores below 40: {ids}. "
                f"Targeted soil sampling is recommended for these fields."
            )
        elif len(low_soil) > 0:
            for _, r in low_soil.iterrows():
                findings.append(
                    f"Field {r['field_id']} has a soil condition screening score of "
                    f"{r['soil_condition_screening_score']:.0f}/100 "
                    f"(pH {r.get('soil_ph', 'N/A')}, "
                    f"OM {r.get('organic_matter_pct', 'N/A')}%). "
                    f"Targeted soil sampling is recommended to guide amendment decisions."
                )

    if "conservation_priority_score" in df.columns:
        top_cons = df.nlargest(2, "conservation_priority_score")
        for _, r in top_cons.iterrows():
            evidence_parts = []
            drainage = r.get("drainage_class", "")
            om = r.get("organic_matter_pct", "N/A")
            erosion = r.get("erosion_risk", "")
            if _safe(drainage) and str(drainage).strip().lower() in (
                "poorly drained", "very poorly drained", "somewhat poorly drained"
            ):
                evidence_parts.append(f"drainage ({drainage})")
            if _safe(erosion) and str(erosion).strip().lower() in ("high", "severe"):
                evidence_parts.append(f"erosion risk ({erosion})")
            if _safe(om) and om < 3.0:
                evidence_parts.append(f"low OM ({om:.1f}%)")

            evidence_note = ""
            if evidence_parts:
                evidence_note = " Evidence: " + "; ".join(evidence_parts) + "."
            findings.append(
                f"Field {r['field_id']} has a conservation priority score of "
                f"{r['conservation_priority_score']:.0f}/100."
                + evidence_note
            )

    if not findings:
        findings.append("No fields require immediate priority attention based on available data.")

    return findings
