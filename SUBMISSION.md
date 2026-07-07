# Project submission

## One-line

**When Rivers Speak** is an interactive marimo dashboard that transforms USGS
river monitoring data into a national-scale river observatory.

## Short blurb

When Rivers Speak is an interactive marimo dashboard that transforms USGS river
monitoring data into a national-scale river observatory. It combines historical
time series, latest observations, anomaly detection, and high-performance
mapping to help users explore how rivers change across states, seasons, and
hydrologic conditions. A DuckDB/Parquet backend keeps national data fast, and
the app queries only small filtered slices so exploration stays responsive.

It is a situational-awareness and exploratory data tool — not a flood-prediction
system.

## Highlights

- **Real public data** from the USGS Water Services API (Site, Daily, and
  Instantaneous Values).
- **Six-tab dashboard:** National River Pulse, Historical Explorer, State
  Comparison, River Change Detector, Data Coverage Observatory, About the Data.
- **Composite anomaly score** blending percentile extremeness, rapid change,
  seasonal deviation, persistence, and a data-completeness penalty.
- **High-performance maps** via deck.gl (point, H3-hex, selected-site layers).
- **Fast backend:** partitioned Parquet + precomputed DuckDB summary tables.
- **Deployable:** Docker-based Hugging Face Space; small bundled sample for an
  instant demo; reproducible full-mode rebuild.

## Tech

marimo · DuckDB · Parquet (PyArrow) · Altair · pydeck / deck.gl · H3 · pandas ·
requests. Python 3.11+, MIT-licensed.

## Links

- Repository: `https://github.com/rodrigosf672/when-rivers-speak`
- Live app: `https://huggingface.co/spaces/rodrigosf672/when-rivers-speak`
