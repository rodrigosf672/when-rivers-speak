# Deployment

The primary hosting target is **Hugging Face Spaces** (Docker SDK), because the
dashboard needs server-side Python and DuckDB/Parquet querying. GitHub Pages is
suitable only for docs or a small static WASM demo (see the roadmap).

## Hugging Face Spaces (recommended)

The repository is ready to deploy as-is. The YAML front matter at the top of
`README.md` configures the Space, and `Dockerfile` serves the app on port 7860.

### One-time setup

1. Create a new Space at <https://huggingface.co/new-space>, SDK **Docker**.
2. Push this repo to the Space's git remote:

   ```bash
   git remote add space https://huggingface.co/spaces/rodrigosf672/when-rivers-speak
   git push space main
   ```

3. The Space builds the image. During the build it:
   - installs the package (`pip install -e .`),
   - builds the DuckDB from the bundled sample (`scripts/build_database.py`),
   - starts `marimo run app.py --host 0.0.0.0 --port 7860`.

The front matter (`sdk: docker`, `app_port: 7860`) must stay at the very top of
`README.md`; Hugging Face reads it there.

### What ships to the Space

- Code (`rivers/`, `app.py`, `scripts/`)
- The small bundled sample (`data/sample/parquet/**`)
- Docs and assets

The DuckDB file is **not** committed; it is built during the image build. Large
or full-mode data never ships (see `.gitignore`).

## Automated mirror (optional)

`.github/workflows/deploy-notes.yml` has a `deploy` job that mirrors the repo to
your Space on every push to `main`, using the Hugging Face Hub API. It is a
no-op unless you configure:

- **Secret** `HF_TOKEN` — a Hugging Face write token.
- **Variable** `HF_SPACE` — e.g. `rodrigosf672/when-rivers-speak`.

Set these under the repo's *Settings → Secrets and variables → Actions*.

## Local Docker

```bash
docker build -t when-rivers-speak .
docker run --rm -p 7860:7860 when-rivers-speak
# open http://localhost:7860
```

## Scheduled data refresh

`.github/workflows/update-data.yml` runs every 6 hours (and on demand). It
refreshes the *latest observations* slice and rebuilds the DuckDB, committing
only the small `data/sample/parquet/latest` partitions. It never commits the
DuckDB file or full-mode data. For larger datasets, migrate storage to a
Hugging Face Dataset (roadmap) rather than committing to git.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `RIVERS_DATA_MODE` | `demo` | `demo` or `full` |
| `RIVERS_DATA_DIR` | `data/sample` | base dir with `parquet/` and `rivers.duckdb` |
| `RIVERS_CACHE_DIR` | `.cache` | raw HTTP response cache |

## Troubleshooting

- **App starts but shows no data.** Ensure the DuckDB exists
  (`python scripts/build_database.py`) or that `data/sample/parquet` is present
  so the app can self-build on first run.
- **`ModuleNotFoundError: app`.** Run from the repo root, or set `PYTHONPATH=.`
  (the served Docker `CMD` runs from `/app`, where this is already the case).
- **Maps blank.** pydeck needs a browser; static previews in `assets/` are for
  the README. In the served app the deck.gl layers render client-side.
