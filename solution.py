"""Automatic plot alignment using satellite edges and rough boundary hints."""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import rasterio
from rasterio.vrt import WarpedVRT
from scipy.ndimage import distance_transform_edt, sobel
from shapely import make_valid
from shapely.affinity import translate


def _ring_points(coords, spacing_m: float) -> np.ndarray:
    """Sample a coordinate ring at roughly uniform metric spacing."""
    coords = np.asarray(coords, dtype=float)
    if len(coords) < 2:
        return np.empty((0, 2), dtype=float)

    starts = coords[:-1]
    vectors = coords[1:] - starts
    lengths = np.linalg.norm(vectors, axis=1)
    samples = []

    for start, vector, length in zip(starts, vectors, lengths):
        count = max(1, int(np.ceil(length / spacing_m)))
        fractions = np.arange(count, dtype=float) / count
        samples.append(start + fractions[:, None] * vector)

    return np.vstack(samples) if samples else np.empty((0, 2), dtype=float)


def _boundary_points(geometry, spacing_m: float, max_points: int = 500) -> np.ndarray:
    """Sample exterior and interior polygon rings for raster scoring."""
    geometry = make_valid(geometry)
    polygons = [geometry] if geometry.geom_type == "Polygon" else list(geometry.geoms)
    parts = []

    for polygon in polygons:
        if polygon.geom_type != "Polygon":
            continue
        parts.append(_ring_points(polygon.exterior.coords, spacing_m))
        parts.extend(_ring_points(ring.coords, spacing_m) for ring in polygon.interiors)

    points = [part for part in parts if len(part)]
    if not points:
        return np.empty((0, 2), dtype=float)

    result = np.vstack(points)
    if len(result) > max_points:
        indices = np.linspace(0, len(result) - 1, max_points, dtype=int)
        result = result[indices]
    return result


def _candidate_offsets(
    prior_dx: float,
    prior_dy: float,
    radius_m: float,
    step_m: float,
) -> np.ndarray:
    residuals = np.arange(-radius_m, radius_m + step_m / 2, step_m)
    offsets = [
        (prior_dx + dx, prior_dy + dy)
        for dx in residuals
        for dy in residuals
    ]
    offsets.extend([(0.0, 0.0), (prior_dx, prior_dy)])
    return np.unique(np.round(offsets, 6), axis=0)


def _score_offsets(
    points: np.ndarray,
    offsets: np.ndarray,
    boundary_distance_m: np.ndarray | None,
    imagery_distance_m: np.ndarray,
    inverse_transform,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Score translated edges against imagery and optional boundary hints."""
    shifted_x = points[:, 0][None, :] + offsets[:, 0][:, None]
    shifted_y = points[:, 1][None, :] + offsets[:, 1][:, None]

    cols = np.floor(
        inverse_transform.a * shifted_x
        + inverse_transform.b * shifted_y
        + inverse_transform.c
    ).astype(int)
    rows = np.floor(
        inverse_transform.d * shifted_x
        + inverse_transform.e * shifted_y
        + inverse_transform.f
    ).astype(int)

    valid = (
        (rows >= 0)
        & (rows < imagery_distance_m.shape[0])
        & (cols >= 0)
        & (cols < imagery_distance_m.shape[1])
    )
    imagery_distances = np.full(rows.shape, 30.0, dtype=np.float32)
    imagery_distances[valid] = imagery_distance_m[rows[valid], cols[valid]]
    imagery_support = np.mean(imagery_distances <= 3.6, axis=1)
    imagery_score = np.mean(np.exp(-0.5 * (imagery_distances / 4.8) ** 2), axis=1)

    if boundary_distance_m is None:
        return imagery_score, imagery_support, np.zeros_like(imagery_support)

    boundary_distances = np.full(rows.shape, 30.0, dtype=np.float32)
    boundary_distances[valid] = boundary_distance_m[rows[valid], cols[valid]]
    boundary_support = np.mean(boundary_distances <= 3.6, axis=1)
    boundary_score = np.mean(np.exp(-0.5 * (boundary_distances / 4.8) ** 2), axis=1)

    scores = 0.65 * imagery_score + 0.35 * boundary_score
    return scores, imagery_support, boundary_support


def _load_edge_distances(village):
    """Create metric distance maps from imagery edges and boundary hints."""
    reference_path = village.boundaries_path or village.imagery_path
    with rasterio.open(reference_path) as reference:
        transform = reference.transform
        raster_crs = reference.crs
        width = reference.width
        height = reference.height
        pixel_size = (abs(reference.res[1]), abs(reference.res[0]))

        boundary_distance_m = None
        if village.boundaries_path is not None:
            boundary_pixels = reference.read(1) > 0
            boundary_distance_m = distance_transform_edt(
                ~boundary_pixels,
                sampling=pixel_size,
            ).astype(np.float32)

    with rasterio.open(village.imagery_path) as imagery:
        with WarpedVRT(
            imagery,
            crs=raster_crs,
            transform=transform,
            width=width,
            height=height,
            resampling=rasterio.enums.Resampling.bilinear,
        ) as vrt:
            red = vrt.read(1, out_dtype="float32")
            green = vrt.read(2, out_dtype="float32")
            blue = vrt.read(3, out_dtype="float32")

    valid = (red + green + blue) > 0
    gray = 0.299 * red + 0.587 * green + 0.114 * blue
    del red, green, blue
    grad_x = sobel(gray, axis=1, mode="nearest")
    grad_y = sobel(gray, axis=0, mode="nearest")
    gradient = np.hypot(grad_x, grad_y)
    del gray, grad_x, grad_y

    valid_gradients = gradient[valid]
    threshold = float(np.percentile(valid_gradients, 84)) if valid_gradients.size else 0.0
    imagery_edges = valid & (gradient >= threshold)
    imagery_distance_m = distance_transform_edt(
        ~imagery_edges,
        sampling=pixel_size,
    ).astype(np.float32)

    return boundary_distance_m, imagery_distance_m, transform, raster_crs


def _estimate_village_shift(
    plots: gpd.GeoDataFrame,
    boundary_distance_m: np.ndarray | None,
    imagery_distance_m: np.ndarray,
    inverse_transform,
) -> tuple[float, float]:
    """Estimate a coherent shift from raster evidence, never public truths."""
    offsets = _candidate_offsets(0.0, 0.0, radius_m=30.0, step_m=3.0)
    sample_count = min(240, len(plots))
    sample_indices = np.linspace(0, len(plots) - 1, sample_count, dtype=int)
    accumulated = np.zeros(len(offsets), dtype=np.float64)
    used = 0

    for index in sample_indices:
        geometry = make_valid(plots.geometry.iloc[index])
        points = _boundary_points(geometry, spacing_m=5.0, max_points=220)
        if len(points) < 4:
            continue
        scores, _, _ = _score_offsets(
            points,
            offsets,
            boundary_distance_m,
            imagery_distance_m,
            inverse_transform,
        )
        scale = float(np.ptp(scores))
        if scale > 1e-6:
            accumulated += (scores - float(scores.min())) / scale
            used += 1

    if not used:
        return 0.0, 0.0
    best_dx, best_dy = offsets[int(np.argmax(accumulated / used))]
    return float(best_dx), float(best_dy)


def _area_reliability(row) -> float:
    """Return a soft reliability factor from drawn versus recorded total area."""
    recorded = row.get("recorded_area_sqm")
    if recorded is None or not np.isfinite(recorded) or recorded <= 0:
        return 0.8
    pot_kharaba = row.get("pot_kharaba_ha")
    if pot_kharaba is not None and np.isfinite(pot_kharaba):
        recorded += float(pot_kharaba) * 10000.0
    ratio = float(row.geometry.area / recorded)
    return float(np.exp(-abs(np.log(max(ratio, 1e-6)))))


def build_predictions(
    village,
    search_radius_m: float = 18.0,
    search_step_m: float = 3.0,
) -> gpd.GeoDataFrame:
    """Align every plot independently, using imagery as the primary signal."""
    boundary_distance_m, imagery_distance_m, transform, raster_crs = _load_edge_distances(village)
    plots = village.plots.to_crs(raster_crs).copy()
    inverse_transform = ~transform
    prior_dx, prior_dy = _estimate_village_shift(
        plots,
        boundary_distance_m,
        imagery_distance_m,
        inverse_transform,
    )
    offsets = _candidate_offsets(
        prior_dx,
        prior_dy,
        radius_m=search_radius_m,
        step_m=search_step_m,
    )
    output_rows = []

    for plot_number, row in plots.iterrows():
        original = make_valid(row.geometry)
        points = _boundary_points(original, spacing_m=2.5)

        if len(points) < 4:
            output_rows.append(
                {
                    "plot_number": str(plot_number),
                    "status": "flagged",
                    "confidence": 0.0,
                    "method_note": "flagged: geometry has too few usable edge points",
                    "geometry": original,
                }
            )
            continue

        scores, imagery_supports, boundary_supports = _score_offsets(
            points,
            offsets,
            boundary_distance_m,
            imagery_distance_m,
            inverse_transform,
        )
        order = np.argsort(scores)
        best_index = int(order[-1])
        best_dx, best_dy = offsets[best_index]
        best_score = float(scores[best_index])
        imagery_support = float(imagery_supports[best_index])
        boundary_support = float(boundary_supports[best_index])
        ambiguity_margin = best_score - float(np.percentile(scores, 90))

        zero_index = int(np.argmin(np.linalg.norm(offsets, axis=1)))
        improvement = best_score - float(scores[zero_index])
        shift_m = float(np.hypot(best_dx, best_dy))
        area_reliability = _area_reliability(row)

        confidence = float(
            np.clip(
                0.05
                + 0.45 * imagery_support
                + 0.20 * boundary_support
                + 1.8 * max(ambiguity_margin, 0)
                + 1.2 * max(improvement, 0)
                + 0.10 * area_reliability,
                0.0,
                0.95,
            )
        )

        should_correct = (
            imagery_support >= 0.10
            and improvement >= 0.012
            and shift_m <= 35.0
            and area_reliability >= 0.45
        )

        if should_correct:
            geometry = translate(original, xoff=best_dx, yoff=best_dy)
            status = "corrected"
            note = (
                f"imagery-led local search dx={best_dx:.1f}m dy={best_dy:.1f}m; "
                f"image_support={imagery_support:.2f}, hint_support={boundary_support:.2f}"
            )
        else:
            geometry = original
            status = "flagged"
            confidence = 0.0
            note = (
                f"flagged: weak, ambiguous, or area-inconsistent evidence; "
                f"image_support={imagery_support:.2f}, improvement={improvement:.3f}"
            )

        output_rows.append(
            {
                "plot_number": str(plot_number),
                "status": status,
                "confidence": confidence,
                "method_note": note,
                "geometry": geometry,
            }
        )

    predictions = gpd.GeoDataFrame(output_rows, geometry="geometry", crs=raster_crs)
    return predictions.to_crs("EPSG:4326")
