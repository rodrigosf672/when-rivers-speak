# Roadmap

The current release is a working, deployable observatory over discharge and
gage height for a sample of states. Planned directions:

## Data breadth
- Light up the remaining registered parameters in the UI (water temperature,
  pH, dissolved oxygen, specific conductance, turbidity) — the fetchers and
  schema already support them; they appear automatically once data is built.
- Expand from the sample states to a full national build.
- Longer historical baselines for more robust day-of-year climatology.

## Storage & scale
- Move large/full-mode datasets to a **Hugging Face Dataset** instead of git,
  and have the app pull cached Parquet at build time.
- Optional DuckDB spatial extension for server-side geospatial filtering.

## Mapping
- A river-network (flowline) layer for context.
- Time-slider / animated map of recent change in the River Change Detector.
- Basemap tiles and configurable projections.

## Analytics
- Calibrated, per-parameter anomaly weighting instead of a single heuristic.
- Trend detection (multi-year) alongside the short-term change scores.
- Uncertainty display for provisional vs. approved values.

## Product
- Shareable deep links that encode the current filter selection.
- CSV / PNG export buttons on every tab.
- A small static **WASM demo** on GitHub Pages for a zero-backend preview,
  with the full server-side app on Hugging Face Spaces.

## Quality
- Unit tests for `normalize`, `metrics`, and `anomalies` on fixture payloads.
- A scheduled build-health check that fails loudly if USGS schemas drift.

Contributions and suggestions are welcome — open an issue describing the river
question you wish the observatory could answer.
