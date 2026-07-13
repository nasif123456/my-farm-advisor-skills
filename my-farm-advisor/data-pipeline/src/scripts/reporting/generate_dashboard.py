#!/usr/bin/env python3
"""CLI entry point for offline Grower Field Weather Dashboard generation.

Usage:
    python scripts/reporting/generate_dashboard.py \\
        --farm-dir ~/path/to/farm

    python scripts/reporting/generate_dashboard.py \\
        --farm-dir ~/path/to/farm \\
        --output ~/path/to/output.html \\
        --no-basemap

    python scripts/reporting/generate_dashboard.py  (auto-discovery)

Can also be invoked as:
    python scripts/farm_dashboard.py dashboard generate --farm-dir <path>
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

_LOCAL_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(_LOCAL_LIB))

from dashboard_generator import generate_dashboard, discover_farm_dir  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate a self-contained offline Grower Field Weather Dashboard HTML file."
    )
    parser.add_argument(
        "--farm-dir",
        default=None,
        help="Explicit path to a farm output directory containing "
        "boundary/field_boundaries.geojson and fields/",
    )
    parser.add_argument(
        "--growers-dir",
        default=None,
        help="Explicit path to the growers root directory (parent of <grower>/farms/<farm>/...)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Explicit output path for the generated HTML file. "
        "Defaults to <farm-dir>/derived/dashboards/<farm-id>_dashboard.html",
    )
    parser.add_argument(
        "--no-basemap",
        action="store_true",
        help="Skip satellite basemap acquisition and generate with a neutral background",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(message)s",
        stream=sys.stderr,
    )

    try:
        out = generate_dashboard(
            farm_dir_path=args.farm_dir,
            output_path=args.output,
            growers_dir=args.growers_dir,
            no_basemap=args.no_basemap,
        )
        print(out)
    except (ValueError, FileNotFoundError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
