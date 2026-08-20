# The build context is the repository root, not server/: the API depends on
# ../vendor/twstock as an editable path dependency, so both trees have to keep
# their relative layout inside the image.
#
#   docker build -f server/Dockerfile .
#
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app/server

# Dependency layer first: manifests and the vendored source only, so editing
# app/ does not invalidate the (slow) dependency resolution below.
COPY vendor/twstock /app/vendor/twstock
COPY server/pyproject.toml server/uv.lock ./
RUN uv sync --frozen --no-install-project

COPY server/app ./app
RUN uv sync --frozen

# Run the venv's interpreter directly -- no `uv run` wrapper at container start.
ENV PATH="/app/server/.venv/bin:$PATH"

# Nothing is written to disk at runtime -- prices and the listed-instrument
# table both live in PostgreSQL -- so an unprivileged user is enough. Note that
# this is exactly why twstock's own `__update_codes()` cannot be used to refresh
# the listing: it rewrites CSVs inside the root-owned package directory. See
# app/services/code_sync.py.
RUN useradd --system --uid 1000 appuser
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health').read()"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
