# BhuMe Boundary Alignment

Submission for the [BhuMe boundary take-home](https://hiring.bhume.in/).

The method moves official cadastral polygons onto field edges visible in the
satellite mosaic. It uses `imagery.tif` as the primary signal, treats
`boundaries.tif` as a rough supporting hint, assigns confidence, and keeps the
original geometry when the evidence is weak or inconsistent.

## Approach

1. Reproject plots and imagery to the boundary-raster metric grid.
2. Extract strong satellite-image gradients as candidate field edges.
3. Convert imagery edges and optional boundary hints into distance maps.
4. Estimate a village-wide translation from a deterministic sample of plots.
5. Search nearby x/y translations independently for every plot.
6. Score candidates using 65% imagery evidence and 35% boundary-hint evidence.
7. Calculate confidence from image support, hint support, score improvement,
   candidate ambiguity, and recorded-area consistency.
8. Flag weak or area-inconsistent cases and retain their official geometry.

Public `example_truths.geojson` records are used only for the final self-score.
They are not read by the prediction algorithm.

## Setup

Python 3.12 is expected. With `uv`:

```bash
uv sync
```

Alternatively, with a normal virtual environment:

```bash
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
python main.py data/34855_vadnerbhairav_chandavad_nashik
```

The command writes:

```text
data/34855_vadnerbhairav_chandavad_nashik/predictions.geojson
```

Optional search controls:

```bash
python main.py data/34855_vadnerbhairav_chandavad_nashik --radius 18 --step 3
```

## Public Check

For the six Nashik examples, the current output reports:

```text
median IoU:          0.897 (official: 0.612)
median improvement:  0.284
median centroid error: 3.378 m
improved fraction:   1.000
confidence Spearman: 0.577
```

This sample is too small to represent hidden performance, especially
calibration and restraint.

## Limitations

- The current geometry model supports translation, not edge-by-edge reshaping.
- Satellite gradients can respond to crop texture, roads, trees, and buildings.
- The rough boundary raster can be sparse under canopy or noisy near settlements.
- Area consistency is a restraint clue, not proof that a plot is correctly drawn.
- Only Vadnerbhairav, Nashik is attempted in this repository.

## Key Files

- `bhume/solution.py`: reusable imagery-led alignment, confidence, and flagging logic.
- `main.py`: complete submission entry point, analogous to `quickstart.py`.
- `bhume/`: starter-kit loading, geospatial helpers, writer, and scorer.
- `CONTRACT.md`: exact input/output schema supplied with the assignment.
