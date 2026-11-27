FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir "uv>=0.5.0"

COPY pyproject.toml uv.lock* README.md alembic.ini ./
COPY apps/api ./apps/api
COPY packages ./packages
COPY workers ./workers
COPY skills ./skills
COPY database ./database
COPY infrastructure/docker/api-entrypoint.sh /usr/local/bin/api-entrypoint.sh

RUN chmod +x /usr/local/bin/api-entrypoint.sh \
    && if [ -f uv.lock ]; then uv sync --frozen --no-dev; else uv sync --no-dev; fi

EXPOSE 8000

HEALTHCHECK --interval=10s --timeout=5s --retries=5 --start-period=10s \
  CMD sh -c 'curl -fsS "http://127.0.0.1:${PORT:-8000}/healthz"' || exit 1

ENTRYPOINT ["/usr/local/bin/api-entrypoint.sh"]
