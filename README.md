# BhuMe Boundary Alignment

This repository contains my solution to the BhuMe engineering take-home: for each cadastral
plot in a village, decide whether the official (georeferenced) boundary can be nudged onto the
real field visible in satellite imagery — and if so, where it should go and how confident the
correction is.

## 1. The Problem

Maharashtra's village land maps were originally hand-drawn at scales of 1:4,000–1:10,000 and
later georeferenced onto satellite imagery by MRSAC. Because the original drawings were never
tied to GPS, each plot's official outline sits a few metres away from the real field on the
ground — a **drift**. The task is to look at the satellite image for each plot and:

- **Correct** it (move/reshape the outline onto the real field) when there's enough evidence to
  do so confidently, or
- **Flag** it (leave the official shape alone) when the evidence is weak, ambiguous, or the
  problem looks like an *area* mismatch rather than a *placement* mismatch (moving an outline
  can't fix a shape that disagrees with the recorded area).

The two error types worth distinguishing:

| Kind of wrong | What it means | Fixable by moving? |
|---|---|---|
| **Placement** | Shape is roughly right, sitting in the wrong spot | Yes |
| **Area** | Drawn shape disagrees with the recorded 7/12 area | No |

A quick signal: `drawn area ÷ recorded area` near 1.0 suggests a placement problem; far from
1.0 suggests an area/record problem.

## 2. Tech Stack

| Tool / Library | Used for |
|---|---|
| Python 3.12 | Core implementation language |
| uv | Dependency management and environment setup (`uv sync`, `uv run`) |
| geopandas | Tabular handling of plot geometries (read/write GeoJSON, CRS reprojection) |
| shapely | Geometry operations — translation, validity checks, boundary point sampling |
| rasterio | Reading GeoTIFF imagery and boundary-hint rasters, windowed reads, `WarpedVRT` reprojection |
| pyproj | Coordinate transforms between EPSG:4326 (lon/lat) and EPSG:3857 (imagery) |
| numpy | Array/grid maths — distance maps, candidate-offset scoring |
| scipy | `sobel` edge detection, `distance_transform_edt`, `spearmanr` for calibration scoring |
| pillow (PIL) | Saving the example image patch in `quickstart.py` |
| GeoJSON / GeoTIFF | Input plot outlines + output predictions (GeoJSON); satellite imagery + boundary hints (GeoTIFF) |
| EPSG:4326 / EPSG:3857 | Coordinate systems for plots/output (lon, lat) and imagery (web-mercator metres) respectively |

## 3. Data

Each village bundle contains:

| File | Contents | Role |
|---|---|---|
| `input.geojson` | Official plot outlines (EPSG:4326), `plot_number`, recorded areas, survey/holding breakdown | Starting geometry — what gets transformed |
| `imagery.tif` | Satellite photo of the village (EPSG:3857) | Primary signal — ground truth reference |
| `boundaries.tif` | Auto-detected (rough) field edges | Secondary hint — strong on open fields, weak under tree cover/buildings |
| `example_truths.geojson` | A handful of hand-checked correct boundaries | Self-scoring only (not used to fit the model) |

Villages used:

| Village | District | Plots | Extent | Median plot | Image detail |
|---|---|---|---|---|---|
| Vadnerbhairav | Nashik | 2,457 | 54.2 km² | 7,753 m² | ~1.2 m/px |
| Malatavadi | Kolhapur | 2,508 | 5.8 km² | 872 m² | ~0.6 m/px |

## 4. Starter Kit (`bhume/`)

The provided package handles all the geospatial plumbing so effort goes into the alignment
logic itself:

- `load(village_dir)` — loads plots + imagery + boundary hints + example truths into a `Village`,
  with CRS handling already sorted (plots in EPSG:4326, imagery in EPSG:3857).
- `patch_for_plot(src, geom)` — crops the satellite image to the area around one plot.
- `lonlat_to_pixel` / `pixel_to_lonlat` — convert between map coordinates and image pixels.
- `write_predictions(path, gdf)` — writes a contract-valid `predictions.geojson`.
- `score(preds, village)` — runs the same accuracy / calibration / restraint checks used for
  grading, against the public example truths (a rough, directional self-check only).
- `global_median_shift(village)` — the naive baseline: estimates **one** translation from the
  example truths and applies it to every plot. Helps a lot of plots at once (most drift is a
  coherent per-village offset) but misses anything that drifted differently (rotation, local
  stretch, outliers) — that gap is the floor this solution tries to beat.

## 5. My Approach (`bhume/solution.py`)

`build_predictions(village)` aligns every plot independently using the satellite imagery as the
primary signal and the rough boundary hints as a secondary nudge.

### 5.1 Edge evidence

For both the imagery and the boundary-hint raster, a **distance-to-edge map** is built:

1. Convert the imagery to grayscale and run a Sobel gradient to find strong edges (top ~16% of
   gradient magnitude).
2. If `boundaries.tif` is available, treat its nonzero pixels as a second edge layer.
3. For each layer, compute a Euclidean distance transform so every pixel knows how far it is
   (in metres) from the nearest detected edge.

### 5.2 Village-level prior shift

Before touching individual plots, a coarse **village-wide shift** is estimated: a sample of
~240 plots has its boundary points tested against a grid of candidate translations
(±30 m, 3 m steps), scored by how well each candidate's edges line up with the imagery/boundary
distance maps. The offset that scores best on average becomes the prior — this captures the
"single coherent drift" that the median-shift baseline also exploits, but derived purely from
raster evidence (never from the example truths).

### 5.3 Per-plot local search

For each plot:

1. Sample its boundary (exterior + interior rings) at ~2.5 m spacing.
2. Search a local grid of translations centred on the village prior (default ±18 m radius,
   3 m step).
3. Score each candidate offset as a weighted blend of imagery-edge support (65%) and
   boundary-hint support (35%) — both expressed as both a "fraction of points within 3.6 m of an
   edge" (support) and a smooth Gaussian-decay score.
4. Pick the best-scoring offset and measure:
   - **Improvement** — how much better the best offset scores than no shift at all.
   - **Ambiguity margin** — how much the best score beats the 90th-percentile score (a sharp
     winner vs. a flat, ambiguous landscape).
   - **Area reliability** — how well the plot's drawn area matches its recorded area
     (`recorded_area_sqm` + `pot_kharaba_ha`), via `exp(-|log(drawn/recorded)|)`. This is the
     placement-vs-area signal from the problem write-up: plots whose shape disagrees with the
     record are penalised even if the imagery search finds *some* offset.

### 5.4 Correct vs. flag decision

A plot is **corrected** only if all of:

- imagery support ≥ 0.10 (some real edge agreement, not noise)
- improvement ≥ 0.012 (the found offset is meaningfully better than doing nothing)
- shift magnitude ≤ 35 m (rules out runaway/implausible jumps)
- area reliability ≥ 0.45 (the shape roughly agrees with the recorded area — a placement
  problem, not an area problem)

Otherwise the plot is **flagged** and the original geometry is kept unchanged — including plots
with too few boundary points to evaluate at all.

### 5.5 Confidence

For corrected plots, confidence is a weighted, clipped combination of:

```
confidence = clip(
    0.05
  + 0.45 * imagery_support
  + 0.20 * boundary_support
  + 1.8  * max(ambiguity_margin, 0)
  + 1.2  * max(improvement, 0)
  + 0.10 * area_reliability,
  0, 0.95
)
```

The intent: confidence should be high only when the imagery edges strongly and unambiguously
agree with the chosen offset, the shift represents a real improvement, and the area record is
consistent with the shape. Flagged plots get confidence `0.0`. This is what the calibration
metric (Spearman / AUC of confidence vs. IoU) checks — a flat or random confidence scores ~0.5
(useless); the goal is for high-confidence corrections to actually be the most accurate ones.

### 5.6 `method_note`

Every prediction carries a short note recording either the applied shift and the imagery/hint
support that justified it, or the reason a plot was flagged (too few points, or
weak/ambiguous/area-inconsistent evidence).

## 6. Output

For each village attempted, `predictions.geojson` is written to
`data/<village>/predictions.geojson` — a GeoJSON `FeatureCollection` in EPSG:4326 with:

| Field | Required | Meaning |
|---|---|---|
| `plot_number` | always | Copied exactly from the input |
| `status` | always | `"corrected"` or `"flagged"` |
| `confidence` | if corrected | 0–1, calibrated against likely accuracy |
| `method_note` | optional | Brief explanation of the shift or the flag reason |
| `geometry` | always | New boundary if corrected, original if flagged |


## 7. Running It

```bash
uv sync
uv run quickstart.py data/34855_vadnerbhairav_chandavad_nashik   # worked example, 1 plot + score
uv run main.py data/34855_vadnerbhairav_chandavad_nashik         # full village run + self-score
```

Optional tuning flags on `main.py`:

```bash
uv run main.py data/<village> --radius 18 --step 3
```

`--radius` is the local search radius (metres) around the village prior shift; `--step` is the
translation grid step (metres).

## 8. Self-Scoring & Limitations

`score()` reports median IoU (predicted vs. official, vs. the example truths), median centroid
error, the fraction of corrected plots that improved, and calibration (Spearman correlation and
AUC of confidence vs. accuracy). These numbers are computed over only a handful of public
example truths (6 for Vadnerbhairav, 3 for Malatavadi) and are a **rough directional check
only** — the real evaluation uses a larger hidden set, and the method was deliberately *not*
tuned against the public examples to avoid overfitting to them.

Known limitations / honest caveats:

- The boundary hints (`boundaries.tif`) are noisy and largely unreliable under tree cover or
  near buildings; the imagery edge signal is weighted more heavily (65/35) for this reason.
- Plots with very few boundary vertices (after simplification) can't be reliably searched and
  are flagged outright.
- The area-reliability check is a heuristic — it doesn't distinguish *why* a plot's drawn and
  recorded areas disagree (subdivision after the original survey, digitisation error, or a
  genuinely wrong shape); it simply treats large disagreement as a reason for restraint.
- The same single method (no per-village hand-tuning) is applied to both villages, in line with
  the "generalises" criterion in the rubric.

## 9. Repository Structure

```
.
├── bhume/
│   ├── __init__.py      # package exports
│   ├── io.py             # load/write village bundles, predictions
│   ├── geo.py            # CRS handling, imagery patches, pixel<->lonlat
│   ├── baseline.py        # naive global median-shift baseline
│   ├── score.py           # self-scoring against example truths
│   └── solution.py        # this submission's alignment method
├── data/
│   └── <village>/
│       ├── input.geojson
│       ├── imagery.tif
│       ├── boundaries.tif
│       ├── example_truths.geojson
│       └── predictions.geojson   # generated output
├── main.py                # CLI entry point: load -> predict -> write -> score
├── quickstart.py           # short worked example (load -> patch -> baseline -> score)
└── transcripts/            # AI chat transcripts used during this take-home
```

## 10. AI Usage & Submission Contents

AI tools were used throughout this take-home — both to understand the problem (reviewing the
assignment write-up, glossary, and scoring rubric) and to develop and refine the alignment
method in `bhume/solution.py`. Full transcripts are included under `/transcripts`
(see `transcripts/README.md` for any web-chat share links).
