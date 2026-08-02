# quant-loop Dockerfile — production image for live trading infrastructure.
#
# Multi-stage build:
#   1. builder  — install pinned dependencies into /install
#   2. runtime  — slim image with only the app code + installed packages
#
# The image intentionally does NOT bake secrets, exchange keys, or strategy
# parameters.  Those are injected at runtime via environment variables or
# Docker secrets (see docker-compose.yml).
#
# References:
# - PyPA recommended layout for pinned installs: pip install -r requirements-lock.txt
# - Docker best-practice: slim runtime image, non-root user, no build tools.

# ---- Stage 1: builder -------------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build

# Build-time only: pip needs these to compile wheels for numpy/scipy/pyarrow.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-lock.txt ./

# Install into a prefix we can COPY to the runtime stage.
RUN pip install --no-cache-dir --prefix=/install \
    -r requirements-lock.txt

# ---- Stage 2: runtime -------------------------------------------------------
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="quant-loop"
LABEL org.opencontainers.image.description="Quant trading loop infrastructure"
LABEL org.opencontainers.image.source="https://github.com/he-mark-qinglong/quant-loop"

# Runtime dependencies: libgomp1 (numpy/scipy OpenMP), ca-certificates (TLS).
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r quant && useradd -r -g quant -m -d /home/quant quant

# Copy installed packages from builder.
COPY --from=builder /install /usr/local

# Copy application code.
WORKDIR /app
COPY _shared/     _shared/
COPY strategies/  strategies/
COPY execution/   execution/

# State directories (mounted as volumes in compose, but create defaults).
RUN mkdir -p /app/state /app/logs && chown -R quant:quant /app

USER quant

# Default entrypoint: start the strategy runner.
# Override in docker-compose.yml per-service.
ENTRYPOINT ["python3", "-m"]
CMD ["_shared.ops.supervisor"]
