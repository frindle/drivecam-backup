import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
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
    get_all_cloud_providers,
    get_cloud_provider,
    create_cloud_provider,
    update_cloud_provider,
    delete_cloud_provider,
    test_cloud_provider as db_test_cloud_provider,
    is_clip_imported,
    filter_already_imported,
    mark_clips_imported,
    update_imported_clip_paths,
    update_clip_duration,
    update_clip_size,
    mark_reencoded,
    get_reencoded_ids,
    delete_clip_from_db,
    delete_clips_by_event_key,
    get_retention_settings,
    set_retention,
    delete_retention,
    get_settings,
    set_settings,
)
from .models import (
    Clip, ClipListResponse, EventSummary, EventListResponse,
    HealthResponse, ScanResponse,
    RemoteShareCreate, RemoteShareUpdate, RemoteShareResponse, RemoteShareTestResponse,
    CloudProviderCreate, CloudProviderUpdate, CloudProviderResponse, CloudProviderType,
    UploadResponse, StorageStatsResponse, ReassignRequest,
)
from .scanner import scan_folder, scan_remote_share, clip_id, byte_count_fmt
from .services import get_cloud_client
from .services.smb_client import SMBClient
from .services.ftp_client import FTPClient
from .services.nfs_client import NFSClient
from .thumbnails import CACHE_DIR, _check_ffmpeg, _get_cache_path, _use_cuda, ffmpeg_available, generate_thumbnail, get_duration

SHARE_PATH = os.environ.get("SHARE_PATH", "/share")
DATA_PATH = os.environ.get("DATA_PATH", "/data")
STATIC_PATH = os.environ.get("STATIC_PATH", "/app/backend/static")
INGEST_PATH = os.path.join(DATA_PATH, "ingest")

DATA_PATHS = [p for p in [SHARE_PATH, DATA_PATH] if p]

app = FastAPI(title="DriveCam Web Viewer", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs(CACHE_DIR, exist_ok=True)


MIME_TYPES = {
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".ts": "video/mp2t",
    ".avi": "video/x-msvideo",
    ".mkv": "video/x-matroska",
}


def _get_mime_type(filename: str) -> str:
    return MIME_TYPES.get(Path(filename).suffix.lower(), "video/mp4")


def _build_urls(clip: Clip) -> Clip:
    if clip.source and clip.source not in ("local", "smb", "ftp", "nfs"):
        return clip
    if clip.source in (None, "local"):
        rel = urllib.parse.quote(clip.relativePath, safe="/")
        clip.downloadUrl = f"/api/clips/{rel}/video"
        clip.thumbnailUrl = f"/api/clips/{rel}/thumbnail"
    else:
        clip_id_enc = clip.id
        clip.downloadUrl = f"/api/shares/{clip.share_id}/clips/{clip_id_enc}/video"
        clip.thumbnailUrl = f"/api/shares/{clip.share_id}/clips/{clip_id_enc}/thumbnail"
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
_ingest_last_moved: dict = {}  # vehicle_folder -> datetime


def _count_ingest_files(folder: Path) -> int:
    return sum(1 for p in folder.rglob("*") if p.is_file())


def _all_files_stable(folder: Path, min_age_secs: float = 10.0) -> bool:
    now = time.time()
    for p in folder.rglob("*"):
        if p.is_file():
            try:
                if now - p.stat().st_mtime < min_age_secs:
                    return False
            except OSError:
                pass
    return True


def _move_ingest_folder(vehicle_folder: str, ingest_subdir: Path) -> int:
    dest_base = Path(DATA_PATH) / vehicle_folder
    moved = 0
    for src in sorted(ingest_subdir.rglob("*")):
        if not src.is_file():
            continue
        rel = src.relative_to(ingest_subdir)
        dst = dest_base / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.move(str(src), str(dst))
            moved += 1
        except Exception as e:
            print(f"Ingest move error {src} -> {dst}: {e}")
    for root, dirs, files in os.walk(ingest_subdir, topdown=False):
        rp = Path(root)
        if rp == ingest_subdir:
            continue
        try:
            if not any(rp.iterdir()):
                rp.rmdir()
        except Exception:
            pass
    return moved


def _ingest_watcher():
    while True:
        time.sleep(30)
        try:
            ingest = Path(INGEST_PATH)
            if not ingest.is_dir():
                continue
            total_moved = 0
            for entry in os.scandir(ingest):
                if not entry.is_dir():
                    continue
                subdir = Path(entry.path)
                if not _count_ingest_files(subdir):
                    continue
                if not _all_files_stable(subdir):
                    continue
                moved = _move_ingest_folder(entry.name, subdir)
                if moved:
                    _ingest_last_moved[entry.name] = datetime.utcnow()
                    print(f"Ingest: moved {moved} files from {entry.name} to storage")
                    total_moved += moved
            if total_moved:
                with _scan_lock:
                    if not _is_scanning:
                        threading.Thread(target=_refresh_cache_locked, daemon=True).start()
        except Exception as e:
            print(f"Ingest watcher error: {e}")


_nvenc_available: Optional[bool] = None
_reencoding = False


def _check_nvenc() -> bool:
    global _nvenc_available
    if _nvenc_available is not None:
        return _nvenc_available
    if not _use_cuda():
        _nvenc_available = False
        return False
    ffmpeg = _check_ffmpeg()
    if not ffmpeg:
        _nvenc_available = False
        return False
    try:
        result = subprocess.run(
            [ffmpeg, '-f', 'lavfi', '-i', 'nullsrc=s=64x64:r=1', '-t', '1', '-c:v', 'hevc_nvenc', '-f', 'null', '-'],
            capture_output=True, timeout=15,
        )
        _nvenc_available = result.returncode == 0
    except Exception:
        _nvenc_available = False
    print(f"GPU re-encoding (hevc_nvenc): {'available' if _nvenc_available else 'not available, using libx265'}")
    return _nvenc_available


def _reencode_clip(file_path: str, encoder: str) -> Optional[int]:
    """Re-encode a clip to H.265. Returns new file size on success, None on failure."""
    ffmpeg = _check_ffmpeg()
    if not ffmpeg:
        return None
    path = Path(file_path)
    if not path.exists():
        return None
    tmp_path = path.with_suffix('.tmp265.mp4')
    try:
        codec_args = (
            ['-c:v', 'hevc_nvenc', '-preset', 'p4', '-cq', '28']
            if encoder == 'hevc_nvenc'
            else ['-c:v', 'libx265', '-preset', 'medium', '-crf', '28']
        )
        result = subprocess.run(
            [ffmpeg, '-i', str(path), *codec_args, '-c:a', 'copy', '-movflags', '+faststart', '-y', str(tmp_path)],
            capture_output=True, timeout=600,
        )
        if result.returncode != 0 or not tmp_path.exists():
            return None
        new_size = tmp_path.stat().st_size
        if new_size >= path.stat().st_size:
            tmp_path.unlink()
            return None
        tmp_path.replace(path)
        return new_size
    except Exception as e:
        print(f"Re-encode error {path}: {e}")
        return None
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _reencode_old_clips_bg() -> None:
    """Background: re-encode local clips older than 14 days to H.265."""
    global _reencoding
    settings = get_settings()
    if settings.get('reencode_enabled', 'false').lower() != 'true':
        return
    if _reencoding:
        return
    _reencoding = True
    try:
        encoder = 'hevc_nvenc' if _check_nvenc() else 'libx265'
        cutoff = datetime.utcnow() - timedelta(days=14)
        clips, _, _ = get_all_clips(limit=100000)
        reencoded_ids = get_reencoded_ids()
        pending = [
            c for c in clips
            if c.source in (None, 'local')
            and c.id not in reencoded_ids
            and c.timestamp is not None
            and c.timestamp < cutoff
        ]
        if not pending:
            return
        print(f"Re-encode: {len(pending)} clips eligible (>{14}d old), encoder={encoder}")
        done = saved_bytes = 0
        for clip in pending:
            for base in DATA_PATHS:
                full = os.path.join(base, clip.relativePath)
                if os.path.exists(full):
                    orig_size = os.path.getsize(full)
                    new_size = _reencode_clip(full, encoder)
                    if new_size is not None:
                        saved_bytes += orig_size - new_size
                        update_clip_size(clip.id, new_size)
                    mark_reencoded(clip.id)
                    done += 1
                    break
        print(f"Re-encode: complete — {done} clips processed, {byte_count_fmt(saved_bytes)} saved")
    finally:
        _reencoding = False


def _enrich_durations_bg() -> None:
    """Background: run ffprobe on local clips missing duration and persist to DB."""
    if not ffmpeg_available():
        return
    clips, _, _ = get_all_clips(limit=100000)
    pending = [c for c in clips if c.source in (None, "local") and c.duration is None]
    if not pending:
        return
    print(f"Duration enrichment: processing {len(pending)} clips")
    updated = 0
    for clip in pending:
        for base in DATA_PATHS:
            full = os.path.join(base, clip.relativePath)
            if os.path.exists(full):
                duration = get_duration(full)
                if duration is not None:
                    update_clip_duration(clip.id, duration)
                    updated += 1
                break
    print(f"Duration enrichment: updated {updated} clips")


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
        _create_ingest_subfolders()
        threading.Thread(target=_enrich_durations_bg, daemon=True).start()
        threading.Thread(target=_reencode_old_clips_bg, daemon=True).start()
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
        gpuAvailable=_check_nvenc(),
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
    vehicle_folder: Optional[str] = Query(None),
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

    if vehicle_folder and vehicle_folder != "all":
        prefix = vehicle_folder + "/"
        raw_events = [
            e for e in raw_events
            if any(c.relativePath.startswith(prefix) or c.relativePath == vehicle_folder
                   for c in e["clips"])
        ]

    all_clips, _, _ = get_all_clips()
    vehicles = sorted(set(c.vehicle.value for c in all_clips))
    vehicle_folders = sorted({
        c.relativePath.split("/")[0]
        for c in all_clips
        if c.source in (None, "local") and "/" in c.relativePath
    })
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
        vehicleFolders=vehicle_folders,
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
        try:
            _refresh_cache_locked()
        finally:
            global _is_scanning
            _is_scanning = False

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
    full_path = _resolve_clip_path(relative_path)
    if not full_path:
        raise HTTPException(status_code=404, detail="File not found on disk")

    thumb_url = generate_thumbnail(full_path, relative_path, "local", None)
    if thumb_url:
        thumb_path = os.path.join(CACHE_DIR, os.path.basename(thumb_url))
        if os.path.exists(thumb_path):
            return FileResponse(thumb_path, media_type="image/jpeg")

    raise HTTPException(status_code=404, detail="Thumbnail not available")


@app.get("/api/clips/{path:path}/video")
def get_video(path: str, request: Request):
    relative_path = urllib.parse.unquote(path)
    full_path = _resolve_clip_path(relative_path)

    if not full_path:
        raise HTTPException(status_code=404, detail="File not found")

    file_size = os.path.getsize(full_path)
    mime = _get_mime_type(full_path)
    range_header = request.headers.get("range") or request.headers.get("Range")

    if range_header:
        try:
            start_str, end_str = range_header.replace("bytes=", "").split("-", 1)
            start = max(0, int(start_str) if start_str else 0)
            end = min(int(end_str) if end_str else file_size - 1, file_size - 1)
        except ValueError:
            start, end = 0, file_size - 1

        length = end - start + 1
        with open(full_path, "rb") as f:
            f.seek(start)
            data = f.read(length)

        return Response(
            content=data,
            status_code=206,
            media_type=mime,
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
        media_type=mime,
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


def _get_share_clip(share_id: int, clip_id: str):
    shares = get_all_remote_shares()
    share = next((s for s in shares if s["id"] == share_id), None)
    if not share:
        raise HTTPException(status_code=404, detail="Share not found")
    clips, _, _ = get_all_clips()
    clip = next((c for c in clips if c.id == clip_id and c.share_id == share_id), None)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    return share, clip


def _stream_share_file(share: dict, relative_path: str, start: int = 0, end: Optional[int] = None) -> bytes:
    protocol = share["protocol"].lower()
    if protocol == "smb":
        client = SMBClient(
            host=share["host"],
            port=share.get("port") or 445,
            username=share.get("username") or "guest",
            password=share.get("password") or "",
            share_path=share.get("path") or "/",
        )
        connected = client.connect()
        if not connected:
            raise HTTPException(status_code=500, detail="Failed to connect to SMB share")
        try:
            return client.download_file_range(relative_path, start, end)
        finally:
            client.disconnect()
    elif protocol == "ftp":
        client = FTPClient(
            host=share["host"],
            port=share.get("port") or 21,
            username=share.get("username") or "anonymous",
            password=share.get("password") or "",
        )
        connected = client.connect()
        if not connected:
            raise HTTPException(status_code=500, detail="Failed to connect to FTP server")
        try:
            return client.download_file_range(relative_path, start, end)
        finally:
            client.disconnect()
    elif protocol == "nfs":
        client = NFSClient(
            host=share["host"],
            port=share.get("port") or 2049,
            path=share.get("path") or "/",
        )
        connected = client.connect()
        if not connected:
            raise HTTPException(status_code=500, detail="Failed to mount NFS share")
        try:
            return client.download_file_range(relative_path, start, end)
        finally:
            client.disconnect()
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported protocol: {protocol}")


@app.get("/api/shares/{share_id}/clips/{clip_id}/video")
def get_share_video(share_id: int, clip_id: str, range: Optional[str] = Query(None)):
    share, clip = _get_share_clip(share_id, clip_id)
    rel_path = clip.relativePath

    if range:
        try:
            start_str, end_str = range.replace("bytes=", "").split("-", 1)
            start = max(0, int(start_str) if start_str else 0)
            end = max(start, int(end_str) if end_str else clip.size - 1)
        except ValueError:
            start, end = 0, clip.size - 1

        data = _stream_share_file(share, rel_path, start, end)
        return Response(
            content=data,
            status_code=206,
            media_type="video/mp4",
            headers={
                "Content-Range": f"bytes {start}-{start + len(data) - 1}/{clip.size}",
                "Accept-Ranges": "bytes",
                "Content-Length": str(len(data)),
            },
        )
    else:
        data = _stream_share_file(share, rel_path)
        return Response(
            content=data,
            media_type="video/mp4",
            headers={
                "Content-Length": str(clip.size),
                "Accept-Ranges": "bytes",
            },
        )


@app.get("/api/shares/{share_id}/clips/{clip_id}/thumbnail")
def get_share_thumbnail(share_id: int, clip_id: str):
    share, clip = _get_share_clip(share_id, clip_id)

    cache_path = _get_cache_path(clip.relativePath, clip.source, share_id)
    if os.path.exists(cache_path):
        return FileResponse(cache_path, media_type="image/jpeg")

    rel_path = clip.relativePath
    data = _stream_share_file(share, rel_path)

    os.makedirs(CACHE_DIR, exist_ok=True)
    ffmpeg = _check_ffmpeg()
    if not ffmpeg:
        raise HTTPException(status_code=500, detail="FFmpeg not available")

    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name

    try:
        hwaccel = ["-hwaccel", "cuda"] if _use_cuda() else []
        result = subprocess.run(
            [
                ffmpeg, "-y",
                "-skip_frame", "nokey",
                *hwaccel,
                "-i", tmp_path,
                "-frames:v", "1",
                "-q:v", "2",
                cache_path,
            ],
            capture_output=True, timeout=60,
        )
        if result.returncode == 0 and os.path.exists(cache_path):
            return FileResponse(cache_path, media_type="image/jpeg")
    except subprocess.SubprocessError:
        pass
    finally:
        os.unlink(tmp_path)

    raise HTTPException(status_code=404, detail="Thumbnail not available")


 # ─── Cloud Providers ──────────────────────────────────────────

@app.get("/api/cloud/providers", response_model=List[CloudProviderResponse])
def list_cloud_providers():
    providers = get_all_cloud_providers()
    return [
        CloudProviderResponse(
            id=p["id"],
            name=p["name"],
            provider=p["provider"],
            folder_path=p["folder_path"],
            enabled=p["enabled"],
            connected=p.get("connected", False),
            clip_count=p.get("clip_count", 0),
            last_sync=p["last_sync"],
            created_at=p["created_at"],
            updated_at=p["updated_at"],
        )
        for p in providers
    ]


@app.post("/api/cloud/providers", response_model=CloudProviderResponse)
def add_cloud_provider(provider: CloudProviderCreate):
    provider_id = create_cloud_provider(
        name=provider.name,
        provider=provider.provider.value,
        credentials=provider.credentials,
        folder_path=provider.folder_path,
    )
    created = get_cloud_provider(provider_id)
    if not created:
        raise HTTPException(status_code=500, detail="Provider was created but could not be retrieved")
    return CloudProviderResponse(
        id=created["id"],
        name=created["name"],
        provider=created["provider"],
        folder_path=created["folder_path"],
        enabled=created["enabled"],
        connected=False,
        clip_count=0,
        last_sync=None,
        created_at=created["created_at"],
        updated_at=created["updated_at"],
    )


@app.put("/api/cloud/providers/{provider_id}", response_model=CloudProviderResponse)
def edit_cloud_provider(provider_id: int, provider: CloudProviderUpdate):
    update_cloud_provider(
        provider_id=provider_id,
        name=provider.name,
        folder_path=provider.folder_path,
        enabled=provider.enabled,
        credentials=provider.credentials,
    )
    updated = get_cloud_provider(provider_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Provider not found")
    return CloudProviderResponse(
        id=updated["id"],
        name=updated["name"],
        provider=updated["provider"],
        folder_path=updated["folder_path"],
        enabled=updated["enabled"],
        connected=False,
        clip_count=updated.get("clip_count", 0),
        last_sync=updated.get("last_sync"),
        created_at=updated["created_at"],
        updated_at=updated["updated_at"],
    )


@app.delete("/api/cloud/providers/{provider_id}")
def remove_cloud_provider(provider_id: int):
    deleted = delete_cloud_provider(provider_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Provider not found")
    return {"status": "deleted"}


@app.post("/api/cloud/providers/{provider_id}/test")
def test_cloud_provider(provider_id: int):
    return db_test_cloud_provider(provider_id)


@app.post("/api/cloud/providers/{provider_id}/sync")
def sync_cloud_provider(provider_id: int):
    from .scanner import clip_id as make_clip_id

    with _scan_lock:
        provider = get_cloud_provider(provider_id)
        if not provider:
            raise HTTPException(status_code=404, detail="Provider not found")

        try:
            client = get_cloud_client(provider["provider"], provider["credentials"], provider["folder_path"])
            raw_clips = client.scan_clips(provider["folder_path"])
        except Exception as e:
            return {"status": "error", "clips_found": 0, "message": str(e)}

        clips = []
        for rc in raw_clips:
            cid = make_clip_id(rc.get("relative_path", rc.get("name", "")))
            clip_id_str = f"cloud:{provider_id}:{cid}"
            clips.append(Clip(
                id=clip_id_str,
                filename=rc["name"],
                relativePath=rc["relative_path"],
                folder=rc["folder"],
                vehicle=rc["vehicle"],
                eventType=rc["event_type"],
                cameraAngle=rc["camera_angle"],
                timestamp=rc.get("timestamp"),
                eventKey=rc.get("event_key"),
                duration=None,
                size=rc["size"],
                sizeString=rc["size_string"],
                hasThumbnail=False,
                hasVideo=True,
                downloadUrl=f"/api/cloud/clips/{clip_id_str}/video",
                thumbnailUrl=f"/api/cloud/clips/{clip_id_str}/thumbnail",
                source=provider["provider"],
                share_id=provider_id,
            ))

        upsert_clips(clips)
        update_cloud_provider(provider_id, clip_count=len(clips), last_sync=datetime.utcnow().isoformat())
        return {"status": "ok", "clips_found": len(clips)}


@app.get("/api/cloud/clips/{clip_id}/video")
def get_cloud_video(clip_id: str, range: Optional[str] = Query(None)):
    clips, _, _ = get_all_clips()
    clip = next((c for c in clips if c.id == clip_id), None)
    if not clip or not clip.source:
        raise HTTPException(status_code=404, detail="Cloud clip not found")

    provider = get_cloud_provider(clip.share_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Cloud provider not found")

    try:
        client = get_cloud_client(clip.source, provider["credentials"], provider["folder_path"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    def stream_from_cloud():
        for chunk in client.get_file(clip.relativePath):
            yield chunk

    try:
        if range:
            try:
                start, end_str = range.replace("bytes=", "").split("-", 1)
                start = max(0, int(start) if start else 0)
                end = max(start, int(end_str) if end_str else clip.size - 1)
            except ValueError:
                start, end = 0, clip.size - 1

            def range_stream():
                iterator = client.get_file(clip.relativePath)
                yielded = 0
                for chunk in iterator:
                    chunk_len = len(chunk)
                    chunk_start = yielded
                    chunk_end = yielded + chunk_len - 1
                    if chunk_end < start:
                        yielded += chunk_len
                        continue
                    if chunk_start > end:
                        break
                    s = max(0, start - chunk_start)
                    e = min(chunk_len - 1, end - chunk_start)
                    yield chunk[s:e + 1]
                    yielded += chunk_len
                    if yielded > end:
                        break

            return Response(
                content=range_stream(),
                status_code=206,
                media_type="video/mp4",
                headers={
                    "Content-Range": f"bytes {start}-{end}/{clip.size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(end - start + 1),
                },
            )
        else:
            return Response(
                content=stream_from_cloud(),
                media_type="video/mp4",
                headers={
                    "Content-Length": str(clip.size),
                    "Accept-Ranges": "bytes",
                },
            )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/cloud/clips/{clip_id}/thumbnail")
def get_cloud_thumbnail(clip_id: str):
    clips, _, _ = get_all_clips()
    clip = next((c for c in clips if c.id == clip_id), None)
    if not clip or not clip.source:
        raise HTTPException(status_code=404, detail="Cloud clip not found")

    provider = get_cloud_provider(clip.share_id)
    if not provider:
        raise HTTPException(status_code=404, detail="Cloud provider not found")

    try:
        client = get_cloud_client(clip.source, provider["credentials"], provider["folder_path"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        thumb_data = client.get_thumbnail(clip.relativePath)
        if not thumb_data:
            raise HTTPException(status_code=404, detail="Thumbnail not available")
        return Response(content=thumb_data, media_type="image/jpeg")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/cloud/oauth/{provider}/url")
def get_oauth_url(provider: str):
    try:
        provider_enum = CloudProviderType(provider)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Unknown provider: {provider}")
    try:
        client = get_cloud_client(provider_enum.value, {"redirect_uri": "http://localhost:8765/api/cloud/oauth/callback"})
        url = client.get_auth_url("http://localhost:8765/api/cloud/oauth/callback", "state123")
        return {"url": url}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/upload/check")
async def check_already_imported(request: Request):
    body = await request.json()
    paths = body.get("paths", [])
    safe_paths = [p.replace("..", "").lstrip("/") for p in paths]
    imported = set(filter_already_imported(safe_paths))
    on_disk = {p for p in safe_paths if (Path(DATA_PATH) / p).exists()}
    already = sorted(imported | on_disk)
    return {"imported": already}


@app.post("/api/upload")
def upload_clips(paths: str = Form(...), files: List[UploadFile] = File(...)):
    try:
        file_paths = json.loads(paths)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid paths JSON")

    if len(file_paths) != len(files):
        raise HTTPException(status_code=400, detail=f"Path count ({len(file_paths)}) must match file count ({len(files)})")

    saved = []
    skipped = []
    errors = []
    imported_records = []

    for i, (relative_path, file) in enumerate(zip(file_paths, files)):
        safe_path = relative_path.replace("..", "").lstrip("/")

        if is_clip_imported(safe_path):
            skipped.append(safe_path)
            continue

        try:
            dest_path = Path(DATA_PATH) / safe_path
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            file.file.seek(0)
            with open(dest_path, "wb") as out:
                shutil.copyfileobj(file.file, out, length=1024 * 1024)
            size_bytes = dest_path.stat().st_size
            saved.append(safe_path)
            imported_records.append({"file_path": safe_path, "size_bytes": size_bytes})
        except Exception as e:
            errors.append(f"{safe_path}: {e}")

    if imported_records:
        mark_clips_imported(imported_records)
    trigger_scan()

    return {
        "success": len(errors) == 0,
        "saved": len(saved),
        "savedPaths": saved,
        "skipped": len(skipped),
        "skippedPaths": skipped,
        "errors": errors,
        "message": f"Saved {len(saved)} file(s)" + (f", {len(errors)} failed" if errors else "") + (f", {len(skipped)} already imported" if skipped else ""),
    }


@app.get("/api/stats/storage", response_model=StorageStatsResponse)
def storage_stats():
    clips, _, _ = get_all_clips(limit=10000)
    if not clips:
        return StorageStatsResponse(
            total_clips=0, total_size_bytes=0, total_size_string="0 B",
            avg_clip_size_bytes=0, avg_clip_size_string="0 B",
            clips_per_hour=0.0, hours_of_footage=0.0,
            vehicles={}, by_vehicle={},
        )

    total_size = sum(c.size for c in clips)
    avg_clip = total_size // len(clips) if clips else 0
    size_str = byte_count_fmt(total_size) if clips else "0 B"
    avg_str = byte_count_fmt(avg_clip) if clips else "0 B"

    by_vehicle: dict = {}
    for c in clips:
        v = c.vehicle.value
        if v not in by_vehicle:
            by_vehicle[v] = {"clips": 0, "size": 0, "timestamps": []}
        by_vehicle[v]["clips"] += 1
        by_vehicle[v]["size"] += c.size
        if c.timestamp:
            by_vehicle[v]["timestamps"].append(c.timestamp)

    for v in by_vehicle:
        ts = sorted(by_vehicle[v]["timestamps"])
        if len(ts) >= 2:
            span = (ts[-1] - ts[0]).total_seconds() / 3600
            by_vehicle[v]["clips_per_hour"] = by_vehicle[v]["clips"] / span if span > 0 else 0
            by_vehicle[v]["hours_of_footage"] = span
        else:
            by_vehicle[v]["clips_per_hour"] = 0
            by_vehicle[v]["hours_of_footage"] = 0
        del by_vehicle[v]["timestamps"]

    all_ts = [c.timestamp for c in clips if c.timestamp]
    clips_per_hour = 0.0
    hours_of_footage = 0.0
    if len(all_ts) >= 2:
        sorted_ts = sorted(all_ts)
        span_h = (sorted_ts[-1] - sorted_ts[0]).total_seconds() / 3600
        hours_of_footage = span_h
        clips_per_hour = len(clips) / span_h if span_h > 0 else 0

    recording_hours = 0.0
    bytes_per_recording_hour = 0.0
    clips_with_dur = [c for c in clips if c.duration is not None]
    if clips_with_dur:
        total_dur_secs = sum(c.duration for c in clips_with_dur)
        total_size_with_dur = sum(c.size for c in clips_with_dur)
        recording_hours = total_dur_secs / 3600
        if total_dur_secs > 0:
            bytes_per_recording_hour = total_size_with_dur / total_dur_secs * 3600
    else:
        # Fall back to session clustering until ffprobe enrichment completes
        SESSION_GAP_SECS = 300
        ts_size_pairs = sorted(
            [(c.timestamp, c.size) for c in clips if c.timestamp],
            key=lambda x: x[0],
        )
        if ts_size_pairs:
            session_start = ts_size_pairs[0][0]
            session_end = ts_size_pairs[0][0]
            for ts, _ in ts_size_pairs[1:]:
                if (ts - session_end).total_seconds() <= SESSION_GAP_SECS:
                    session_end = ts
                else:
                    recording_hours += max((session_end - session_start).total_seconds() / 3600, 1 / 60)
                    session_start = ts
                    session_end = ts
            recording_hours += max((session_end - session_start).total_seconds() / 3600, 1 / 60)
            if recording_hours > 0:
                bytes_per_recording_hour = total_size / recording_hours

    vehicle_info = {v: {"clips": d["clips"], "size": d["size"]} for v, d in by_vehicle.items()}

    return StorageStatsResponse(
        total_clips=len(clips),
        total_size_bytes=total_size,
        total_size_string=size_str,
        avg_clip_size_bytes=avg_clip,
        avg_clip_size_string=avg_str,
        clips_per_hour=round(clips_per_hour, 1),
        hours_of_footage=round(hours_of_footage, 1),
        bytes_per_recording_hour=round(bytes_per_recording_hour, 0),
        recording_hours=round(recording_hours, 1),
        vehicles=vehicle_info,
        by_vehicle=by_vehicle,
    )


@app.delete("/api/clips/{clip_id}")
def delete_clip(clip_id: str):
    clips, _, _ = get_all_clips(limit=50000)
    clip = next((c for c in clips if c.id == clip_id and c.source in (None, "local")), None)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found or not a local clip")
    full_path = _resolve_clip_path(clip.relativePath)
    if full_path and os.path.exists(full_path):
        os.remove(full_path)
        parent = Path(full_path).parent
        try:
            if not any(parent.iterdir()):
                parent.rmdir()
        except Exception:
            pass
    delete_clip_from_db(clip_id)
    return {"status": "deleted", "id": clip_id}


@app.delete("/api/events/{event_key:path}")
def delete_event(event_key: str):
    event_key = urllib.parse.unquote(event_key)
    clips, _, _ = get_all_clips(limit=50000)
    event_clips = [c for c in clips if c.eventKey == event_key and c.source in (None, "local")]
    if not event_clips:
        raise HTTPException(status_code=404, detail="Event not found or not a local event")
    deleted_files = 0
    for clip in event_clips:
        full_path = _resolve_clip_path(clip.relativePath)
        if full_path and os.path.exists(full_path):
            os.remove(full_path)
            deleted_files += 1
            parent = Path(full_path).parent
            try:
                if not any(parent.iterdir()):
                    parent.rmdir()
            except Exception:
                pass
    removed = delete_clips_by_event_key(event_key)
    return {"status": "deleted", "clips_deleted": removed, "files_deleted": deleted_files}


@app.get("/api/vehicles")
def list_vehicle_folders_from_clips():
    clips, _, _ = get_all_clips(limit=10000)
    folders = sorted({
        clip.relativePath.split("/")[0]
        for clip in clips
        if clip.relativePath and clip.source in (None, "local") and clip.relativePath.split("/")[0]
    })
    return {"folders": folders}


@app.post("/api/vehicles/reassign")
def reassign_vehicle(body: ReassignRequest):
    from_folder = body.from_folder.strip("/")
    to_folder = body.to_folder.strip("/")
    from_path = Path(DATA_PATH) / from_folder
    to_path = Path(DATA_PATH) / to_folder
    if not from_path.exists():
        raise HTTPException(status_code=404, detail=f"Source folder not found: {from_folder}")
    moved, errors = [], []
    for src in from_path.rglob("*"):
        if src.is_file():
            rel = src.relative_to(from_path)
            dst = to_path / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(src), str(dst))
                moved.append((str(Path(from_folder) / rel), str(Path(to_folder) / rel)))
            except Exception as e:
                errors.append(str(e))
    try:
        if from_path.exists() and not any(from_path.iterdir()):
            from_path.rmdir()
    except Exception:
        pass
    if moved:
        update_imported_clip_paths(moved)
    trigger_scan()
    return {"moved": len(moved), "errors": errors}


@app.get("/api/vehicles/folders")
def list_vehicle_folders():
    return {
        "TeslaCam": ["RecentClips", "SavedClips", "SentryClips"],
        "rivian_dashcam": ["dashcam", "saved", "gearguard"],
        "TeslaModel3": ["RecentClips", "SavedClips", "SentryClips"],
        "TeslaModelS": ["RecentClips", "SavedClips", "SentryClips"],
        "TeslaModelX": ["RecentClips", "SavedClips", "SentryClips"],
        "TeslaModelY": ["RecentClips", "SavedClips", "SentryClips"],
    }


@app.get("/api/retention")
def list_retention():
    return get_retention_settings()


@app.put("/api/retention/{vehicle_folder:path}")
def update_retention(vehicle_folder: str, body: dict):
    vehicle_folder = urllib.parse.unquote(vehicle_folder)
    days = body.get("retention_days")
    if days is not None and not isinstance(days, int):
        raise HTTPException(status_code=400, detail="retention_days must be an integer or null")
    set_retention(vehicle_folder, days)
    return {"vehicle_folder": vehicle_folder, "retention_days": days}


@app.delete("/api/retention/{vehicle_folder:path}")
def remove_retention(vehicle_folder: str):
    vehicle_folder = urllib.parse.unquote(vehicle_folder)
    delete_retention(vehicle_folder)
    return {"status": "deleted"}


@app.post("/api/retention/run")
def run_purge_now():
    deleted = _purge_old_clips()
    return {"status": "ok", "deleted": deleted}


def _purge_old_clips() -> int:
    settings = get_retention_settings()
    if not settings:
        return 0
    total_deleted = 0
    clips, _, _ = get_all_clips(limit=50000)
    now = datetime.utcnow()
    for s in settings:
        if s["retention_days"] is None:
            continue
        cutoff = now - timedelta(days=s["retention_days"])
        prefix = s["vehicle_folder"] + "/"
        to_delete = [
            c for c in clips
            if c.source in (None, "local")
            and c.timestamp
            and c.timestamp < cutoff
            and (c.relativePath.startswith(prefix) or c.relativePath == s["vehicle_folder"])
        ]
        for clip in to_delete:
            full_path = _resolve_clip_path(clip.relativePath)
            if full_path and os.path.exists(full_path):
                try:
                    os.remove(full_path)
                    parent = Path(full_path).parent
                    if not any(parent.iterdir()):
                        parent.rmdir()
                except OSError:
                    pass
            delete_clip_from_db(clip.id)
            total_deleted += 1
    if total_deleted:
        print(f"Midnight purge: deleted {total_deleted} clips")
    return total_deleted


def _midnight_scheduler():
    while True:
        now = datetime.now()
        tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        sleep_secs = (tomorrow - now).total_seconds()
        time.sleep(sleep_secs)
        try:
            _purge_old_clips()
        except Exception as e:
            print(f"Midnight purge error: {e}")


@app.get("/api/settings")
def get_app_settings():
    s = get_settings()
    s["data_path"] = DATA_PATH
    s["ingest_path"] = INGEST_PATH
    return s


@app.put("/api/settings")
async def update_app_settings(request: Request):
    body = await request.json()
    allowed = {"app_name"}
    updates = {k: v for k, v in body.items() if k in allowed}
    return set_settings(updates)


@app.get("/api/ingest/status")
def ingest_status():
    ingest = Path(INGEST_PATH)
    pending = {}
    subfolders: set = set()
    if ingest.is_dir():
        for entry in os.scandir(ingest):
            if entry.is_dir():
                subfolders.add(entry.name)
                count = _count_ingest_files(Path(entry.path))
                if count:
                    pending[entry.name] = count
    clips_result, _, _ = get_all_clips(limit=2000)
    known_vehicles = {
        c.relativePath.split("/")[0]
        for c in clips_result
        if c.source in (None, "local") and "/" in c.relativePath
    }
    all_folders = sorted(subfolders | known_vehicles)
    return {
        "ingest_path": INGEST_PATH,
        "subfolders": all_folders,
        "pending": pending,
        "last_moved": {k: v.isoformat() for k, v in _ingest_last_moved.items()},
    }


@app.get("/api/debug/files")
def debug_files(limit: int = Query(50, ge=1, le=500), folder: Optional[str] = Query(None)):
    """Return actual filenames from disk. Use ?folder=RoadCam to filter."""
    results = []
    folder_counts: dict = {}
    for base in DATA_PATHS:
        base_path = Path(base)
        if not base_path.is_dir():
            continue
        for root, dirs, files in os.walk(base_path):
            for fname in files:
                ext = Path(fname).suffix.lower()
                if ext not in {".mp4", ".mov", ".ts", ".avi", ".mkv"}:
                    continue
                try:
                    rel = str(Path(root).relative_to(base_path) / fname)
                except ValueError:
                    rel = fname
                parts = rel.split("/")
                top_folder = parts[1] if len(parts) > 1 else parts[0]
                folder_counts[top_folder] = folder_counts.get(top_folder, 0) + 1
                if folder and folder.lower() not in rel.lower():
                    continue
                if len(results) < limit:
                    results.append(rel)
    return {"count": len(results), "folder_counts": folder_counts, "files": results}


static_dir = Path(STATIC_PATH)
if static_dir.exists():
    app.mount("/", StaticFiles(directory=STATIC_PATH, html=True), name="static")
else:
    @app.get("/")
    def index():
        return {"message": "DriveCam Web Viewer", "docs": "/api/docs"}


def _ensure_ingest_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)
    try:
        os.chmod(path, 0o777)
    except OSError:
        pass


def _create_ingest_subfolders():
    clips, _, _ = get_all_clips(limit=2000)
    folders = {
        c.relativePath.split("/")[0]
        for c in clips
        if c.source in (None, "local") and "/" in c.relativePath
    }
    for folder in folders:
        _ensure_ingest_dir(os.path.join(INGEST_PATH, folder))


@app.on_event("startup")
def startup():
    _ensure_ingest_dir(INGEST_PATH)
    threading.Thread(target=_ingest_watcher, daemon=True).start()
    threading.Thread(target=_midnight_scheduler, daemon=True).start()
    if not get_clip_count():
        threading.Thread(target=_refresh_cache_locked, daemon=True).start()
    else:
        _create_ingest_subfolders()