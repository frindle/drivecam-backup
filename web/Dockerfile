# Build: docker build -t drivecam-web .
# Run:   docker compose up

FROM python:3.11-slim AS base
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app

# ── Stage 1: Build React frontend ───────────────────────
FROM node:20-alpine AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json* ./
RUN npm install
COPY frontend/ ./
RUN npm run build

# ── Stage 2: Assemble ─────────────────────────────────────
FROM base AS production
COPY --from=frontend /src/frontend/dist ./backend/static/
COPY backend/ ./backend/
RUN pip install --no-cache-dir fastapi==0.109.2 uvicorn==0.27.1 pydantic==2.6.1

ENV SHARE_PATH=/share
ENV STATIC_PATH=/app/backend/static

EXPOSE 8000
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]