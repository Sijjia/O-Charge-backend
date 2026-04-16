# ─── Red Petroleum OCPP Server ───
# Multi-stage production build for Coolify deployment
# Build context: ocpp-rp/backend/

# Stage 1: Builder — install Python deps with build tools
FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y \
    gcc g++ libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Stage 2: Runtime — minimal image
FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    dumb-init libpq5 curl fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN useradd -m -u 1000 evpower && \
    mkdir -p /var/log/evpower-ocpp && \
    chown -R evpower:evpower /var/log/evpower-ocpp

# Python venv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Copy application code
COPY --chown=evpower:evpower app/ ./app/
COPY --chown=evpower:evpower ocpp_ws_server/ ./ocpp_ws_server/
COPY --chown=evpower:evpower start.sh ./
RUN sed -i 's/\r$//' start.sh && chmod +x start.sh

USER evpower

# Default env
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production \
    APP_PORT=9210

EXPOSE 9210

HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:${APP_PORT:-9210}/health || exit 1

ENTRYPOINT ["dumb-init", "--"]
CMD ["./start.sh"]
