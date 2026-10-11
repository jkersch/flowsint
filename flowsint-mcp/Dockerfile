# Build from the repository root:
#   docker build -f flowsint-mcp/Dockerfile -t flowsint-mcp .
FROM python:3.12-slim AS builder

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh

ENV PATH="/root/.local/bin:$PATH"

# The whole workspace is needed to resolve the lockfile, but only flowsint-mcp
# and its own dependencies are installed: the server talks to the API over
# HTTP and never imports flowsint-core.
COPY pyproject.toml uv.lock ./
COPY flowsint-types ./flowsint-types
COPY flowsint-core ./flowsint-core
COPY flowsint-enrichers ./flowsint-enrichers
COPY flowsint-api ./flowsint-api
COPY flowsint-mcp ./flowsint-mcp

RUN uv sync --frozen --no-dev --package flowsint-mcp --no-editable

FROM python:3.12-slim AS production

LABEL org.opencontainers.image.source="https://github.com/reconurge/flowsint"
LABEL org.opencontainers.image.description="Flowsint MCP server"
LABEL org.opencontainers.image.licenses="Apache-2.0"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH" \
    FLOWSINT_MCP_TRANSPORT=http \
    FLOWSINT_MCP_HOST=0.0.0.0 \
    FLOWSINT_MCP_PORT=5002

RUN groupadd -g 1001 flowsint && \
    useradd -u 1001 -g flowsint -s /usr/sbin/nologin -m flowsint

WORKDIR /app
COPY --from=builder --chown=flowsint:flowsint /app/.venv ./.venv

USER flowsint

EXPOSE 5002

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5002/health')" || exit 1

CMD ["flowsint-mcp"]
