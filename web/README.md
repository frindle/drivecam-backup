# DriveCam Web Viewer

A self-hosted web viewer for dashcam footage. Reads clips from your NAS share, organizes them by date and event type, and serves a clean timeline UI with video playback.

**Supports:** Tesla (RecentClips / SavedClips / SentryClips), Rivian (dashcam / saved / gearguard)

## Quick Start

```bash
cd web
docker compose up --build
```

Open **http://localhost:8000**. On first load it scans the share and builds a clip cache.

## Mount Your NAS Share

The app scans two paths inside the container — `/share` and `/data`. Mount one or both depending on where your footage lives.

**Single mount** (most common):
```yaml
volumes:
  - /Volumes/DashCam:/share:ro    # macOS
  # - /mnt/dashcam:/share:ro       # Linux
```

**Separate mounts** (if footage is in multiple locations):
```yaml
volumes:
  - /path/to/clips:/share:ro   # TeslaCam / RIVIAN_DASHCAM root
  - /path/to/other:/data:ro    # additional location
```

Both paths are scanned on startup and during rescan. File resolution (video playback, thumbnails) automatically detects which base path a clip belongs to.

## Event Types

| Folder | Event | Description |
|--------|-------|-------------|
| `RecentClips` / `dashcam` | Driving | Auto-recorded while driving |
| `SentryClips` / `gearguard` | Gear Guard | Motion-triggered while parked |
| `SavedClips` / `saved` | Saved | Manually saved by driver |

Gear Guard clips are highlighted with an amber badge.

## Filters

Filter by **vehicle**, **event type**, **folder**, **camera angle**, or **date range**. Clear to reset.

## Keyboard Shortcuts

| Key | Action |
|-----|--------|
| `←` / `→` | Previous / Next clip |
| `Esc` | Close player |

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/clips` | GET | List clips (filter with query params) |
| `/api/health` | GET | Health check + cache status |
| `/api/scan` | POST | Trigger a re-scan of the share |
| `/api/clips/{path}/video` | GET | Stream a video clip |
| `/api/clips/{path}/thumbnail` | GET | Get (or generate) a thumbnail |
| `/api/clips/{path}/duration` | GET | Get video duration in seconds |

## Rescanning

Click **Rescan** in the top-right corner to re-walk the share and refresh the clip list. This runs in the background.

## Troubleshooting

**"No clips found"**
- Verify your mounts are correct inside the container: `docker compose exec drivecam-web ls /share` and `ls /data`
- Click **Rescan** to force a refresh

**Thumbnails show as blank**
- FFmpeg is required (installed automatically in the container)
- Check container logs: `docker compose logs -f`

## Architecture

```
web/
├── backend/
│   ├── main.py          # FastAPI app + routes
│   ├── scanner.py       # Filesystem walker + filename parsing
│   ├── thumbnails.py    # FFmpeg thumbnail generation
│   ├── db.py            # SQLite metadata cache
│   └── models.py        # Pydantic models
├── frontend/
│   ├── src/
│   │   ├── App.jsx      # React timeline UI
│   │   ├── App.css      # Dark theme styles
│   │   └── api.js       # API client
│   └── package.json
├── Dockerfile
└── docker-compose.yml
```

**Backend** — Python FastAPI. Scans both `/share` and `/data` on startup and caches clip metadata in SQLite. Thumbnails are generated on-demand via FFmpeg.

**Frontend** — React 18 + Vite. Dark theme, filterable timeline, keyboard-navigable video player.