#!/usr/bin/env python3
"""CLI entry point for generating a row crop intelligence dashboard."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data_loader import load_dashboard_data, validate_dashboard_data
from app.renderer import render_dashboard
from scripts.prepare_dashboard_data import prepare

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Generate a grower-level row crop intelligence dashboard"
    )
    parser.add_argument("--grower-id", required=True, help="Grower identifier (slug)")
    parser.add_argument("--runtime-dir", required=True,
                        help="Runtime data root (contains data-pipeline/growers/...)")
    parser.add_argument("--year", type=int, default=2024, help="Crop year (default: 2024)")
    parser.add_argument("--output", default=None,
                        help="Output HTML path (default: <runtime-dir>/dashboard_outputs/<grower>/dashboard.html)")
    parser.add_argument("--title", default="Row Crop Intelligence Dashboard",
                        help="Dashboard title")
    parser.add_argument("--skip-prepare", action="store_true",
                        help="Skip data preparation; use existing files")
    parser.add_argument("--data-dir", default=None,
                        help="Pre-prepared data directory (default: <runtime-dir>/dashboard_outputs/<grower>)")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")

    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    rt = Path(args.runtime_dir)

    if args.data_dir:
        data_dir = Path(args.data_dir)
    else:
        data_dir = rt / "dashboard_outputs" / args.grower_id

    if args.output:
        output_path = Path(args.output)
    else:
        output_path = data_dir / "dashboard.html"

    if not args.skip_prepare:
        log.info(f"Preparing dashboard data for grower: {args.grower_id} (year: {args.year})")
        try:
            meta = prepare(str(rt), args.grower_id, args.year, str(data_dir))
            log.info(f"Data prepared: {meta['field_count']} fields")
        except Exception as e:
            log.error(f"Data preparation failed: {e}")
            return 1
    else:
        log.info(f"Using pre-prepared data from: {data_dir}")

    log.info("Loading dashboard data...")
    dash_data = load_dashboard_data(str(data_dir))

    warnings = validate_dashboard_data(dash_data)
    for w in warnings:
        log.warning(w)

    if dash_data.field_summary.empty:
        log.error("No field data available. Cannot generate dashboard.")
        return 1

    log.info(f"Rendering dashboard ({len(dash_data.field_summary)} fields)...")
    render_dashboard(dash_data, str(output_path), title=args.title)
    print(f"\nDashboard generated: {output_path.resolve()}")
    print(f"Open in your browser to view the report.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
