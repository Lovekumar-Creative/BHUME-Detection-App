#!/usr/bin/env python3
"""Create a satellite overview with official and predicted village plots."""

from __future__ import annotations

import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
from PIL import Image, ImageDraw


DEFAULT_VILLAGE = Path("data/34855_vadnerbhairav_chandavad_nashik")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("village_dir", nargs="?", type=Path, default=DEFAULT_VILLAGE)
    parser.add_argument("--width", type=int, default=1600, help="output width in pixels")
    parser.add_argument("--output", type=Path, default=Path("village_overview.png"))
    return parser.parse_args()


def _pixel_ring(transform, coordinates):
    inverse = ~transform
    return [inverse * (x, y) for x, y in coordinates]


def _draw_geometry(draw: ImageDraw.ImageDraw, geometry, transform, color, width: int) -> None:
    if geometry is None or geometry.is_empty:
        return
    polygons = [geometry] if geometry.geom_type == "Polygon" else list(geometry.geoms)
    for polygon in polygons:
        if polygon.geom_type != "Polygon":
            continue
        draw.line(_pixel_ring(transform, polygon.exterior.coords), fill=color, width=width, joint="curve")
        for interior in polygon.interiors:
            draw.line(_pixel_ring(transform, interior.coords), fill=color, width=width)


def main() -> None:
    args = parse_args()
    imagery_path = args.village_dir / "imagery.tif"
    input_path = args.village_dir / "input.geojson"
    predictions_path = args.village_dir / "predictions.geojson"

    with rasterio.open(imagery_path) as src:
        scale = args.width / src.width
        height = max(1, round(src.height * scale))
        rgb = src.read(
            [1, 2, 3],
            out_shape=(3, height, args.width),
            resampling=rasterio.enums.Resampling.bilinear,
        )
        transform = src.transform * src.transform.scale(src.width / args.width, src.height / height)
        raster_crs = src.crs

    image = Image.fromarray(np.moveaxis(rgb, 0, -1).astype(np.uint8)).convert("RGBA")
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    official = gpd.read_file(input_path).to_crs(raster_crs)
    for geometry in official.geometry:
        _draw_geometry(draw, geometry, transform, (255, 70, 70, 170), 1)

    if predictions_path.exists():
        predictions = gpd.read_file(predictions_path).to_crs(raster_crs)
        for row in predictions.itertuples():
            color = (50, 255, 90, 210) if row.status == "corrected" else (255, 215, 0, 220)
            _draw_geometry(draw, row.geometry, transform, color, 2)

    result = Image.alpha_composite(image, overlay).convert("RGB")
    result.save(args.output, quality=95)
    print(f"Saved village plot overview to {args.output.resolve()}")
    print("Red = official, green = corrected, yellow = flagged")


if __name__ == "__main__":
    main()
