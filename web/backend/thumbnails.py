import hashlib
import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

CACHE_DIR = "/tmp/drivecam_thumbnails"

_ffmpeg_path: str | None = None
_ffmpeg_checked = False


def _check_ffmpeg() -> str:
    global _ffmpeg_path, _ffmpeg_checked
    if _ffmpeg_checked:
        return _ffmpeg_path or ""
    _ffmpeg_checked = True
    for candidate in ["/usr/local/bin/ffmpeg", "/usr/bin/ffmpeg", "ffmpeg"]:
        try:
            subprocess.run([candidate, "-version"], capture_output=True, timeout=5)
            _ffmpeg_path = candidate
            return _ffmpeg_path
        except OSError:
            continue
    return ""


def ffmpeg_available() -> bool:
    return bool(_check_ffmpeg())


def _check_cuda() -> bool:
    ffmpeg = _check_ffmpeg()
    if not ffmpeg:
        return False
    try:
        result = subprocess.run(
            [ffmpeg, "-hwaccels"],
            capture_output=True, text=True, timeout=10,
        )
        return "cuda" in result.stdout.lower()
    except Exception:
        return False


def _check_nvidia_smi() -> bool:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except OSError:
        return False


def _use_cuda() -> bool:
    try:
        return _check_cuda() and _check_nvidia_smi()
    except Exception:
        return False


def _get_cache_path(relative_path: str, source: str = "local", share_id: int | None = None) -> str:
    key = f"{source}:{share_id or ''}:{relative_path}"
    h = hashlib.md5(key.encode()).hexdigest()
    return os.path.join(CACHE_DIR, f"{h}.jpg")


def get_duration(file_path: str) -> float | None:
    ffmpeg = _check_ffmpeg()
    if not ffmpeg:
        return None
    try:
        result = subprocess.run(
            [
                ffmpeg, "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                file_path,
            ],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        pass
    return None


def generate_thumbnail(
    file_path: str,
    clip_relative_path: str,
    source: str = "local",
    share_id: int | None = None,
) -> str | None:
    ffmpeg = _check_ffmpeg()
    if not ffmpeg:
        return None

    cache_path = _get_cache_path(clip_relative_path, source, share_id)
    if os.path.exists(cache_path):
        return cache_path

    os.makedirs(CACHE_DIR, exist_ok=True)

    hwaccel = ["-hwaccel", "cuda"] if _use_cuda() else []
    try:
        result = subprocess.run(
            [
                ffmpeg, "-y",
                "-skip_frame", "nokey",
                *hwaccel,
                "-i", file_path,
                "-frames:v", "1",
                "-q:v", "2",
                cache_path,
            ],
            capture_output=True, timeout=60,
        )
        if result.returncode == 0 and os.path.exists(cache_path):
            return cache_path
    except subprocess.SubprocessError:
        pass

    return None