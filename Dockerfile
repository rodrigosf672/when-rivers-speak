# When Rivers Speak — Hugging Face Spaces (Docker SDK) image.
# Serves the marimo dashboard on port 7860 in demo mode with bundled sample data.
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y \
    git \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies first for better layer caching.
COPY pyproject.toml .
COPY rivers ./rivers
RUN pip install --upgrade pip && pip install -e .

# App + bundled demo data + scripts + docs/assets.
COPY app.py .
COPY marimo.toml .
COPY scripts ./scripts
COPY data ./data
COPY assets ./assets
COPY docs ./docs

# Demo mode uses the small bundled sample dataset shipped in the repo.
ENV RIVERS_DATA_MODE=demo \
    RIVERS_DATA_DIR=/app/data/sample \
    PYTHONUNBUFFERED=1

# Build the analytical DuckDB from the bundled Parquet so the app starts
# instantly. If a pre-built rivers.duckdb was shipped with the image (e.g. for a
# large national dataset whose summary build would exceed the container's memory),
# use it as-is and skip the rebuild.
RUN if [ -f data/sample/rivers.duckdb ]; then \
        echo "Using pre-built DuckDB shipped with the image."; \
    else \
        python scripts/build_database.py || echo "DB build skipped (will build on first run)"; \
    fi

EXPOSE 7860

# marimo run serves the notebook as a read-only app (no code editing exposed).
CMD ["marimo", "run", "app.py", "--host", "0.0.0.0", "--port", "7860"]
