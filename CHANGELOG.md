# Changelog

All notable changes to this project will be documented in this file.

## [1.1.0] - 2025-05-20

### Added
- **Cloud provider sync** — iCloud, Google Drive, OneDrive, Dropbox integration with OAuth 2.0 authentication
- **Cloud clip streaming** — video and thumbnail playback directly from cloud providers via `/api/cloud/clips/{id}/video`
- **Cloud Settings UI** — dedicated Cloud tab in settings with provider forms, Test Connection, and Sync buttons
- **Shares/Cloud tabs** — restructured Settings to separate local/remote shares from cloud providers
- **GitHub Actions CI/CD** — auto-builds and pushes Docker image to GHCR on every push to `main`
- **Remote share video streaming** — SMB/FTP/NFS clips now stream via `/api/shares/{id}/clips/{id}/video`
- **GPU-accelerated thumbnails** — FFmpeg CUDA decode (`-hwaccel cuda`) for faster thumbnail generation

### Fixed
- Circular import bug in `thumbnails.py` — `CACHE_DIR` moved to module top, `from .thumbnails import` removed
- Lazy CUDA detection — `_use_cuda()` function instead of module-level `USE_CUDA` constant (subprocess calls no longer run at import time)
- FFmpeg command ordering — `-hwaccel cuda` moved before `-i` input flag (was silently ignored)
- Range request parsing — `bytes=500-` now correctly returns bytes 500→EOF (was returning empty)
- `_scan_lock` race condition — cloud sync now held during scan
- `get_cloud_client` module-level import — no longer called inside function scopes
- OAuth route registration — `@app.get` on `/api/cloud/oauth/{provider}/url` restored
- `import json` at module level in `db.py`

### Changed
- `docker-compose.yml` moved to repo root — single `docker compose up -d` from repo root
- Dockerfile now installs all cloud provider SDKs: `pyicloud`, `google-api-python-client`, `msgraph-sdk`, `dropbox`, `aioftp`

### Technical Details
- Backend: Python FastAPI + SQLite
- Frontend: React 18 + Vite, 170KB bundle
- Docker: Multi-stage build, `nvidia/cuda:12.1.0-runtime-ubuntu22.04`, `runtime: nvidia`
- Thumbnails: Cached at `/tmp/drivecam_thumbnails`, lazy CUDA detection

---

## [1.0.0] - 2025-01-15

### Added
- Web viewer for dashcam footage with Docker-based deployment
- Multi-path support for scanning footage from `/share` or `/data` volume mounts
- Vehicle detection for Tesla and Rivian dashcam footage
- Event filtering by vehicle type, event type, camera angle, folder, and date range
- Timeline UI with dark theme and video player
- Gear Guard event highlighting with amber badge
- HTTP Range support for video streaming and seeking
- FFmpeg thumbnail generation with caching
- SQLite metadata cache for clip information
- React 18 + Vite frontend with FastAPI backend
- Multi-stage Docker build (Node + Python + FFmpeg)
- docker-compose.yml for easy deployment

### Technical Details
- Backend: Python FastAPI with FFmpeg integration
- Frontend: React 18 + Vite
- Database: SQLite for metadata caching
- Thumbnails: Generated on-demand with FFmpeg, cached at `/tmp/drivecam_thumbnails`
- Docker: Multi-stage build with Node.js build stage and Python runtime stage