import os
import threading
import urllib.parse
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .db import (
    clear_cache,
    get_all_clips,
    get_clip_count,
    get_events,
    get_cached_at,
    set_cached_at,
    upsert_clips,
    get_remote_shares,
    create_remote_share,
    update_remote_share,
    delete_remote_share,
    test_remote_share as db_test_remote_share,
    get_all_remote_shares,
)
from .models import (
    Clip, ClipListResponse, EventSummary, EventListResponse,
    HealthResponse, ScanResponse,
    RemoteShareCreate, RemoteShareUpdate, RemoteShareResponse, RemoteShareTestResponse,
)
from .scanner import scan_folder, scan_remote_share, clip_id
from .thumbnails import CACHE_DIR, ffmpeg_available, generate_thumbnail, get_duration

SHARE_PATH = os.environ.get("SHARE_PATH", "/share")
DATA_PATH = os.environ.get("DATA_PATH", "/data")
STATIC_PATH = os.environ.get("STATIC_PATH", "/app/backend/static")

DATA_PATHS = [p for p in [SHARE_PATH, DATA_PATH] if p]

app = FastAPI(title="DriveCam Web Viewer", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs(CACHE_DIR, exist_ok=True)


def _build_urls(clip: Clip) -> Clip:
    path_enc = clip.relativePath.replace("/", "%2F")
    clip.downloadUrl = f"/api/clips/{path_enc}/video"
    clip.thumbnailUrl = f"/api/clips/{path_enc}/thumbnail"
    return clip


def _get_filtered_clips(
    clips: List[Clip],
    vehicle: Optional[str] = None,
    event_type: Optional[str] = None,
    folder: Optional[str] = None,
    camera: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> List[Clip]:
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
_is_scanning = False
_last_scan_at: Optional[datetime] = None


def _refresh_cache_locked() -> None:
    global _is_scanning, _last_scan_at
    try:
        _is_scanning = True
        _last_scan_at = datetime.utcnow()
        local_clips = scan_folder(DATA_PATHS)
        remote_clips = []
        shares = get_remote_shares()
        for share in shares:
            try:
                remote_clips.extend(scan_remote_share(share))
            except Exception as e:
                print(f"Failed to scan remote share {share['name']}: {e}")
        all_clips = {c.id: c for c in local_clips}
        for c in remote_clips:
            all_clips[c.id] = c
        clips = list(all_clips.values())
        upsert_clips(clips)
        set_cached_at(datetime.utcnow())
    finally:
        _is_scanning = False


def _resolve_clip_path(relative_path: str) -> Optional[str]:
    for base in DATA_PATHS:
        base = os.path.realpath(base)
        full = os.path.realpath(os.path.join(base, relative_path))
        try:
            if os.path.exists(full) and os.path.commonpath([full, base]) == base:
                return full
        except ValueError:
            continue
    return None


@app.get("/api/health", response_model=HealthResponse)
def health():
    share_exists = any(Path(p).is_dir() for p in DATA_PATHS)
    return HealthResponse(
        status="ok" if share_exists else "share_missing",
        sharePath=", ".join(DATA_PATHS),
        clipsInCache=get_clip_count(),
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
    before_timestamp: Optional[str] = Query(None, description="Cursor: return clips older than this ISO timestamp"),
    limit: int = Query(200, ge=1, le=500),
):
    all_clips, has_more, oldest_ts = get_all_clips(before_timestamp=before_timestamp, limit=limit)
    clips = _get_filtered_clips(list(all_clips), vehicle, event_type, folder, camera, date_from, date_to)
    page_clips = clips[:limit]
    clips_has_more = len(clips) > limit
    page_oldest_ts = page_clips[-1].timestamp.isoformat() if page_clips and page_clips[-1].timestamp else oldest_ts
    vehicles = sorted(set(c.vehicle.value for c in all_clips))
    folders = sorted(set(c.folder for c in all_clips))
    event_types = sorted(set(c.eventType.value for c in all_clips))
    cameras = sorted(set(c.cameraAngle.value for c in all_clips))

    return ClipListResponse(
        clips=page_clips,
        total=len(clips),
        vehicles=vehicles,
        folders=folders,
        eventTypes=event_types,
        cameras=cameras,
        cachedAt=get_cached_at(),
        hasMore=clips_has_more,
        oldestTimestamp=page_oldest_ts,
    )


@app.get("/api/events", response_model=EventListResponse)
def list_events(
    vehicle: Optional[str] = Query(None),
    event_type: Optional[str] = Query(None),
    folder: Optional[str] = Query(None),
    camera: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    before_timestamp: Optional[str] = Query(None, description="Cursor: return events older than this ISO timestamp"),
    limit: int = Query(100, ge=1, le=200),
):
    raw_events, has_more, oldest_ts = get_events(
        before_timestamp=before_timestamp,
        limit=limit,
        vehicle=vehicle,
        event_type=event_type,
        folder=folder,
        camera=camera,
        date_from=date_from,
        date_to=date_to,
    )

    all_clips, _, _ = get_all_clips()
    vehicles = sorted(set(c.vehicle.value for c in all_clips))
    folders = sorted(set(c.folder for c in all_clips))
    event_types = sorted(set(c.eventType.value for c in all_clips))
    cameras = sorted(set(c.cameraAngle.value for c in all_clips))

    event_summaries = []
    for ev in raw_events:
        clips_out = [_build_urls(c) for c in ev["clips"]]
        thumb_clip = next((c for c in clips_out if c.cameraAngle.value == "front"), clips_out[0])
        event_summaries.append(EventSummary(
            eventKey=ev["eventKey"],
            timestamp=ev["timestamp"],
            vehicle=ev["vehicle"],
            eventType=ev["eventType"],
            folder=ev["folder"],
            cameraAngles=ev["cameraAngles"],
            cameraCount=ev["cameraCount"],
            totalSize=ev["totalSize"],
            sizeString=ev["sizeString"],
            thumbnailUrl=thumb_clip.thumbnailUrl if thumb_clip else None,
            clips=clips_out,
        ))

    return EventListResponse(
        events=event_summaries,
        total=len(event_summaries),
        vehicles=vehicles,
        folders=folders,
        eventTypes=event_types,
        cameras=cameras,
        cachedAt=get_cached_at(),
        hasMore=has_more,
        oldestTimestamp=oldest_ts,
    )


@app.post("/api/scan", response_model=ScanResponse)
def trigger_scan():
    global _is_scanning
    with _scan_lock:
        if _is_scanning:
            return ScanResponse(status="already_scanning", clipsFound=get_clip_count(), cacheUpdated=False)

    def do_scan():
        _refresh_cache_locked()

    t = threading.Thread(target=do_scan, daemon=True)
    t.start()
    return ScanResponse(status="scanning", clipsFound=get_clip_count(), cacheUpdated=False)


@app.get("/api/scan/status")
def scan_status():
    with _scan_lock:
        scanning = _is_scanning
    return {"scanning": scanning, "lastScanAt": _last_scan_at}


@app.get("/api/clips/{path:path}/thumbnail")
def get_thumbnail(path: str):
    relative_path = urllib.parse.unquote(path)
    cid = clip_id(relative_path)
    clips, _, _ = get_all_clips()
    clip = next((c for c in clips if c.id == cid), None)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found in cache")

    full_path = _resolve_clip_path(relative_path)
    if not full_path:
        raise HTTPException(status_code=404, detail="File not found on disk")

    thumb_url = generate_thumbnail(full_path, relative_path, os.path.dirname(full_path))
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
            start = max(0, int(start) if start else 0)
            end = max(start, int(end) if end else file_size - 1)
        except ValueError:
            start, end = 0, file_size - 1

        end = min(end, file_size - 1)

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


 # ─── Remote Shares ──────────────────────────────────────────

@app.get("/api/shares", response_model=List[RemoteShareResponse])
def list_shares():
    return get_all_remote_shares()


@app.post("/api/shares", response_model=RemoteShareResponse)
def create_share(share: RemoteShareCreate):
    share_id = create_remote_share(
        name=share.name,
        protocol=share.protocol,
        host=share.host,
        path=share.path,
        port=share.port,
        username=share.username,
        password=share.password,
    )
    created = get_all_remote_shares()
    created_share = next((s for s in created if s["id"] == share_id), None)
    if not created_share:
        raise HTTPException(status_code=500, detail="Share was created but could not be retrieved")
    return created_share


@app.put("/api/shares/{share_id}", response_model=RemoteShareResponse)
def update_share(share_id: int, share: RemoteShareUpdate):
    update_remote_share(
        share_id=share_id,
        name=share.name,
        protocol=share.protocol,
        host=share.host,
        port=share.port,
        username=share.username,
        password=share.password,
        path=share.path,
        enabled=share.enabled,
    )
    shares = get_all_remote_shares()
    updated = next((s for s in shares if s["id"] == share_id), None)
    if not updated:
        raise HTTPException(status_code=404, detail="Share not found")
    return updated


@app.delete("/api/shares/{share_id}")
def remove_share(share_id: int):
    deleted = delete_remote_share(share_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Share not found")
    return {"status": "deleted"}


@app.post("/api/shares/{share_id}/test", response_model=RemoteShareTestResponse)
def test_share(share_id: int):
    result = db_test_remote_share(share_id)
    return result


static_dir = Path(STATIC_PATH)
if static_dir.exists():
    app.mount("/", StaticFiles(directory=STATIC_PATH, html=True), name="static")
else:
    @app.get("/")
    def index():
        return {"message": "DriveCam Web Viewer", "docs": "/api/docs"}


@app.on_event("startup")
def startup():
    if not get_clip_count():
        _refresh_cache_locked()