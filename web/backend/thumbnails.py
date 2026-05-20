import hashlib
import os
import subprocess
from typing import Optional

THUMBNAIL_SIZE = "320x180"
THUMBNAIL_TIME = "00:00:01"
CACHE_DIR = "/tmp/drivecam_thumbnails"

_ffmpeg_checked = False
_ffmpeg_works = False
_cuda_checked = False
_cuda_available = False
_nvidia_smi_checked = False
_nvidia_smi_available = False


def _get_cache_path(clip_relative_path: str) -> str:
    h = hashlib.md5(clip_relative_path.encode()).hexdigest()
    return os.path.join(CACHE_DIR, f"{h}.jpg")


def _check_cuda() -> bool:
    global _cuda_checked, _cuda_available
    if _cuda_checked:
        return _cuda_available
    _cuda_checked = True
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-hwaccels"],
            capture_output=True, text=True, timeout=5
        )
        _cuda_available = "cuda" in result.stdout.lower()
    except:
        _cuda_available = False
    return _cuda_available


def _check_nvidia_smi() -> bool:
    global _nvidia_smi_checked, _nvidia_smi_available
    if _nvidia_smi_checked:
        return _nvidia_smi_available
    _nvidia_smi_checked = True
    try:
        subprocess.run(["nvidia-smi"], capture_output=True, timeout=5)
        _nvidia_smi_available = True
    except:
        _nvidia_smi_available = False
    return _nvidia_smi_available


def ffmpeg_available() -> bool:
    global _ffmpeg_checked, _ffmpeg_works
    if _ffmpeg_checked:
        return _ffmpeg_works
    _ffmpeg_checked = True
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        _ffmpeg_works = True
    except (subprocess.SubprocessError, FileNotFoundError):
        _ffmpeg_works = False
    return _ffmpeg_works


def get_duration(file_path: str) -> Optional[float]:
    for hwaccel in (["-hwaccel", "cuda"] if _check_cuda() and _check_nvidia_smi() else [], []):
        try:
            cmd = [
                "ffprobe", "-v", "error",
                *hwaccel,
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                file_path
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return float(result.stdout.strip())
        except (subprocess.SubprocessError, ValueError):
            pass
    return None


def generate_thumbnail(file_path: str, clip_relative_path: str, share_path: str) -> Optional[str]:
    cache_path = _get_cache_path(clip_relative_path)
    if os.path.exists(cache_path):
        return f"/thumbnails/{os.path.basename(cache_path)}"

    os.makedirs(CACHE_DIR, exist_ok=True)
    if not os.path.exists(file_path):
        return None

    use_cuda = _check_cuda() and _check_nvidia_smi()

    try:
        if use_cuda:
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-hwaccel", "cuda",
                    "-i", file_path,
                    "-ss", THUMBNAIL_TIME,
                    "-vframes", "1",
                    "-vf", f"scale_cuda={THUMBNAIL_SIZE}:force_original_aspect_ratio=decrease",
                    "-q:v", "3",
                    cache_path,
                ],
                capture_output=True,
                timeout=30,
            )
        else:
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", file_path,
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