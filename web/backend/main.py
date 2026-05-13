import os
import threading
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .db import (
    clear_cache,
    get_all_clips,
    get_cached_at,
    set_cached_at,
    upsert_clips,
)
from .models import Clip, ClipListResponse, HealthResponse, ScanResponse
from .scanner import scan_folder, clip_id
from .thumbnails import CACHE_DIR, ffmpeg_available, generate_thumbnail, get_duration

SHARE_PATH = os.environ.get("SHARE_PATH", "/share")
DATA_PATH = os.environ.get("DATA_PATH", "/data")
STATIC_PATH = os.environ.get("STATIC_PATH", "/app/backend/static")

DATA_PATHS = [p for p in [SHARE_PATH, DATA_PATH] if p]

app = FastAPI(title="DriveCam Web Viewer", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs(CACHE_DIR, exist_ok=True)


def _build_urls(clip: Clip) -> Clip:
    path_enc = clip.relativePath.replace("/", "%2F")
    clip.downloadUrl = f"/api/clips/{path_enc}/video"
    clip.thumbnailUrl = f"/api/clips/{path_enc}/thumbnail"
    clip.hasThumbnail = True
    return clip


def _get_filtered_clips(
    vehicle: Optional[str] = None,
    event_type: Optional[str] = None,
    folder: Optional[str] = None,
    camera: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Clip]:
    clips = get_all_clips()
    for clip in clips:
        _build_urls(clip)

    if vehicle and vehicle != "all":
        clips = [c for c in clips if c.vehicle.value == vehicle]
    if event_type and event_type != "all":
        clips = [c for c in clips if c.eventType.value == event_type]
    if folder and folder != "all":
        clips = [c for c in clips if c.folder == folder]
    if camera and camera != "all":
        clips = [c for c in clips if c.cameraAngle.value == camera]
    if date_from:
        try:
            df = datetime.fromisoformat(date_from)
            clips = [c for c in clips if c.timestamp and c.timestamp >= df]
        except ValueError:
            pass
    if date_to:
        try:
            dt = datetime.fromisoformat(date_to)
            clips = [c for c in clips if c.timestamp and c.timestamp <= dt]
        except ValueError:
            pass

    return clips


_scan_lock = threading.Lock()


def _refresh_cache() -> None:
    with _scan_lock:
        clips = scan_folder(DATA_PATHS)
        upsert_clips(clips)
        set_cached_at(datetime.utcnow())


def _resolve_clip_path(relative_path: str) -> Optional[str]:
    for base in DATA_PATHS:
        full = os.path.join(base, relative_path)
        if os.path.exists(full):
            return full
    return None


@app.get("/api/health", response_model=HealthResponse)
def health():
    share_exists = any(Path(p).is_dir() for p in DATA_PATHS)
    clips = get_all_clips()
    return HealthResponse(
        status="ok" if share_exists else "share_missing",
        sharePath=", ".join(DATA_PATHS),
        clipsInCache=len(clips),
        ffmpegAvailable=ffmpeg_available(),
    )


@app.get("/api/clips", response_model=ClipListResponse)
def list_clips(
    vehicle: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    folder: Optional[str] = Query(None),
    camera: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
):
    clips = _get_filtered_clips(vehicle, event_type, folder, camera, date_from, date_to)
    all_clips = get_all_clips()
    vehicles = sorted(set(c.vehicle.value for c in all_clips))
    folders = sorted(set(c.folder for c in all_clips))
    event_types = sorted(set(c.eventType.value for c in all_clips))

    return ClipListResponse(
        clips=clips,
        total=len(clips),
        vehicles=vehicles,
        folders=folders,
        eventTypes=event_types,
        cachedAt=get_cached_at(),
    )


@app.post("/api/scan", response_model=ScanResponse)
def trigger_scan():
    def do_scan():
        _refresh_cache()

    t = threading.Thread(target=do_scan, daemon=True)
    t.start()
    return ScanResponse(status="scanning", clipsFound=len(get_all_clips()), cacheUpdated=False)


@app.get("/api/clips/{path:path}/thumbnail")
def get_thumbnail(path: str):
    relative_path = urllib.parse.unquote(path)
    cid = clip_id(relative_path)
    clips = get_all_clips()
    clip = next((c for c in clips if c.id == cid), None)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found in cache")

    full_path = _resolve_clip_path(relative_path)
    if not full_path:
        raise HTTPException(status_code=404, detail="File not found on disk")

    # Generate thumbnail using whichever base path was found
    for base in DATA_PATHS:
        if os.path.exists(os.path.join(base, relative_path)):
            thumb_url = generate_thumbnail(full_path, relative_path, base)
            break
    else:
        thumb_url = None

    if thumb_url:
        thumb_path = os.path.join(CACHE_DIR, os.path.basename(thumb_url))
        if os.path.exists(thumb_path):
            return FileResponse(thumb_path, media_type="image/jpeg")

    raise HTTPException(status_code=404, detail="Thumbnail not available")


@app.get("/api/clips/{path:path}/video")
def get_video(path: str, range: Optional[str] = Query(None)):
    relative_path = urllib.parse.unquote(path)
    full_path = _resolve_clip_path(relative_path)

    if not full_path:
        raise HTTPException(status_code=404, detail="File not found")

    file_size = os.path.getsize(full_path)

    if range:
        try:
            start, end = range.replace("bytes=", "").split("-")
            start = int(start) if start else 0
            end = int(end) if end else file_size - 1
        except ValueError:
            start, end = 0, file_size - 1

        length = end - start + 1
        with open(full_path, "rb") as f:
            f.seek(start)
            data = f.read(length)

        return Response(
            content=data,
            status_code=206,
            media_type="video/mp4",
            headers={
                "Content-Range": f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(length),
            },
        )

    def iterfile():
        with open(full_path, "rb") as f:
            while chunk := f.read(1024 * 1024):
                yield chunk

    return Response(
        content=iterfile(),
        media_type="video/mp4",
        headers={
            "Content-Length": str(file_size),
            "Accept-Ranges": "bytes",
        },
    )


@app.get("/api/clips/{path:path}/duration")
def get_clip_duration(path: str):
    relative_path = urllib.parse.unquote(path)
    full_path = _resolve_clip_path(relative_path)
    if not full_path:
        raise HTTPException(status_code=404, detail="File not found")

    duration = get_duration(full_path)
    if duration is None:
        raise HTTPException(status_code=500, detail="Could not read duration")
    return {"duration": duration}


@app.post("/api/cache/clear")
def clear():
    clear_cache()
    return {"status": "cleared"}


static_dir = Path(STATIC_PATH)
if static_dir.exists():
    app.mount("/", StaticFiles(directory=STATIC_PATH, html=True), name="static")
else:
    @app.get("/")
    def index():
        return {"message": "DriveCam Web Viewer", "docs": "/api/docs"}


@app.on_event("startup")
def startup():
    if not get_all_clips():
        _refresh_cache()