FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY market_engine ./market_engine
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:$PATH"
COPY scheduler.sh ./scheduler.sh

# yfinance keeps a small cache under HOME, so the user needs a real one.
RUN useradd --system --uid 1000 --create-home engine
USER engine

ENTRYPOINT ["/bin/sh", "/app/scheduler.sh"]
