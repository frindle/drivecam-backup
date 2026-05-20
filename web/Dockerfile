# Build: docker build -t drivecam-web .
# Run:   docker compose up
# Requires nvidia-container-toolkit on the host and "nvidia" runtime configured

FROM nvidia/cuda:12.1.0-runtime-ubuntu22.04 AS base
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 python3-pip ffmpeg curl \
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/bin/python3.11 /usr/local/bin/python \
    && ln -s /usr/bin/pip3 /usr/local/bin/pip

WORKDIR /app

# ── Stage 1: Build React frontend ───────────────────────
FROM node:20-alpine AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Assemble ─────────────────────────────────────
FROM base AS production
COPY --from=frontend /src/frontend/dist ./backend/static/
COPY backend/ ./backend/
RUN apt-get update && apt-get install -y --no-install-recommends \
    libkrb5-dev gcc g++ python3-dev libtirpc-dev \
    libxml2-dev libxmlsec1-dev libxmlsec1-openssl pkg-config \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --break-system-packages \
        fastapi==0.109.2 \
        "uvicorn[standard]==0.27.1" \
        pydantic==2.6.1 \
        smbprotocol>=1.12.0 \
        pyicloud>=1.14.0 \
        google-api-python-client>=2.100.0 \
        google-auth-oauthlib>=1.0.0 \
        msgraph-sdk>=1.0.0 \
        dropbox>=11.0.0 \
        aioftp>=0.22.0 \
        python-nfs>=0.2.0

ENV DATA_PATH=/data
ENV SHARE_PATH=/data
ENV STATIC_PATH=/app/backend/static

EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]