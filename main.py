#!/usr/bin/env python3
"""Generate and evaluate BhuMe boundary-alignment predictions.

This root-level entry point mirrors ``quickstart.py``. The reusable alignment
implementation lives in ``bhume.solution``.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from bhume import load, score, write_predictions
from bhume.solution import build_predictions


DEFAULT_VILLAGE = Path("data/34855_vadnerbhairav_chandavad_nashik")


def parse_args() -> argparse.Namespace:
    """Read command-line options."""
    parser = argparse.ArgumentParser(
        description="Generate corrected plot-boundary predictions."
    )
    parser.add_argument(
        "village_dir",
        nargs="?",
        type=Path,
        default=DEFAULT_VILLAGE,
        help=f"village bundle directory (default: {DEFAULT_VILLAGE})",
    )
    parser.add_argument(
        "--radius",
        type=float,
        default=18.0,
        help="local search radius in metres",
    )
    parser.add_argument(
        "--step",
        type=float,
        default=3.0,
        help="translation search step in metres",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.radius <= 0 or args.step <= 0:
        raise SystemExit("--radius and --step must be positive")

    village = load(args.village_dir)
    truth_count = 0 if village.example_truths is None else len(village.example_truths)

    print(f"Loaded village: {village.slug}")
    print(f"Plots: {len(village.plots)}")
    print(f"Example truths: {truth_count}")
    print(f"Boundary hints: {'available' if village.boundaries_path else 'not available'}")
    print(f"Imagery-led search: radius={args.radius:.1f}m, step={args.step:.1f}m")

    predictions = build_predictions(
        village,
        search_radius_m=args.radius,
        search_step_m=args.step,
    )
    output_path = write_predictions(
        village.dir / "predictions.geojson",
        predictions,
    )
    print(f"\nWrote {len(predictions)} predictions to {output_path}")

    if village.example_truths is not None:
        print()
        print(score(predictions, village))
    else:
        print("No example truths found, so scoring was skipped.")


if __name__ == "__main__":
    main()
