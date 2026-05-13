import hashlib
import os
import subprocess
from typing import Optional

THUMBNAIL_SIZE = "320x180"
THUMBNAIL_TIME = "00:00:01"
CACHE_DIR = "/tmp/drivecam_thumbnails"


def _get_cache_path(clip_relative_path: str) -> str:
    h = hashlib.md5(clip_relative_path.encode()).hexdigest()
    return os.path.join(CACHE_DIR, f"{h}.jpg")


def ffmpeg_available() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return True
    except (subprocess.SubprocessError, FileNotFoundError):
        return False


def get_duration(file_path: str) -> Optional[float]:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                file_path
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return float(result.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        return None


def generate_thumbnail(file_path: str, clip_relative_path: str, share_path: str) -> Optional[str]:
    cache_path = _get_cache_path(clip_relative_path)
    if os.path.exists(cache_path):
        return f"/thumbnails/{os.path.basename(cache_path)}"

    os.makedirs(CACHE_DIR, exist_ok=True)
    full_path = os.path.join(share_path, clip_relative_path)
    if not os.path.exists(full_path):
        return None

    try:
        subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", full_path,
                "-ss", THUMBNAIL_TIME,
                "-vframes", "1",
                "-vf", f"scale={THUMBNAIL_SIZE}:force_original_aspect_ratio=decrease,pad={THUMBNAIL_SIZE}:(ow-iw)/2:(oh-ih)/2:black",
                "-q:v", "3",
                cache_path,
            ],
            capture_output=True,
            timeout=30,
        )
        if os.path.exists(cache_path):
            return f"/thumbnails/{os.path.basename(cache_path)}"
    except subprocess.SubprocessError:
        pass

    return None