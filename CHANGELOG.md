# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

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

### Changed
- Updated README.md to include web viewer documentation

### Technical Details
- Backend: Python FastAPI with FFmpeg integration
- Frontend: React 18 + Vite
- Database: SQLite for metadata caching
- Thumbnails: Generated on-demand with FFmpeg, cached at `/tmp/drivecam_thumbnails`
- Docker: Multi-stage build with Node.js build stage and Python runtime stage
