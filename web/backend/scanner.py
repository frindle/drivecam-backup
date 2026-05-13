import hashlib
import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .models import CameraAngle, Clip, EventType, VehicleType

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".ts", ".mkv"}

FOLDER_EVENT_MAP = {
    VehicleType.TESLA: {
        "RecentClips": EventType.DRIVING,
        "SavedClips": EventType.MANUALLY_SAVED,
        "SentryClips": EventType.GEAR_GUARD,
    },
    VehicleType.RIVIAN: {
        "dashcam": EventType.DRIVING,
        "saved": EventType.MANUALLY_SAVED,
        "gearguard": EventType.GEAR_GUARD,
        "gear_guard": EventType.GEAR_GUARD,
        "GearGuard": EventType.GEAR_GUARD,
    },
}

TESLA_FILENAME_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})-(front|back|left|right|cabin)\.mp4$",
    re.IGNORECASE,
)

RIVIAN_FILENAME_RE = re.compile(
    r"^(?:(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2}))"
    r"(?:[_-](front|back|left|right|cabin|driver|passenger))?"
    r"(?:[_-](sentry|gearguard|guard|event))?"
    r"\.(mp4|mov|ts|avi|mkv)$",
    re.IGNORECASE,
)


def detect_vehicle_from_folder(folder_name: str) -> VehicleType:
    fl = folder_name.lower()
    if fl == "teslacam":
        return VehicleType.TESLA
    if fl in ("rivian_dashcam", "rivian", "dashcam", "rivan"):
        return VehicleType.RIVIAN
    return VehicleType.UNKNOWN


def parse_timestamp(filename: str, folder_name: str, vehicle: VehicleType) -> Optional[datetime]:
    if m := TESLA_FILENAME_RE.match(filename):
        date_str = m.group(1)
        time_str = m.group(2).replace("-", ":")
        try:
            return datetime.strptime(f"{date_str}_{time_str}", "%Y-%m-%d_%H:%M:%S")
        except ValueError:
            pass

    if m := RIVIAN_FILENAME_RE.match(filename):
        try:
            year, month, day, hour, minute, second = (
                int(m.group(1)), int(m.group(2)), int(m.group(3)),
                int(m.group(4)), int(m.group(5)), int(m.group(6))
            )
            return datetime(year, month, day, hour, minute, second)
        except (ValueError, TypeError):
            pass
    return None


def parse_camera_angle(filename: str, vehicle: VehicleType) -> CameraAngle:
    fn = filename.lower()
    if vehicle == VehicleType.TESLA:
        if "front" in fn: return CameraAngle.FRONT
        if "back" in fn: return CameraAngle.BACK
        if "left" in fn: return CameraAngle.LEFT
        if "right" in fn: return CameraAngle.RIGHT
        if "cabin" in fn: return CameraAngle.CABIN
    if vehicle == VehicleType.RIVIAN:
        if "front" in fn: return CameraAngle.FRONT
        if "back" in fn or "rear" in fn: return CameraAngle.BACK
        if "left" in fn: return CameraAngle.LEFT
        if "right" in fn: return CameraAngle.RIGHT
        if "cabin" in fn or "interior" in fn or "driver" in fn: return CameraAngle.CABIN
    return CameraAngle.UNKNOWN


def clip_id(relative_path: str) -> str:
    return hashlib.md5(relative_path.encode()).hexdigest()[:12]


def byte_count_fmt(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def scan_folder(base_paths: List[str], clip_folders: Optional[List[str]] = None) -> List[Clip]:
    clips: List[Clip] = []
    for base_path in base_paths:
        clips.extend(_scan_single_path(base_path, clip_folders))
    clips.sort(key=lambda c: c.timestamp or datetime.min, reverse=True)
    return clips


def _scan_single_path(base_path: str, clip_folders: Optional[List[str]] = None) -> List[Clip]:
    share = Path(base_path)
    if not share.is_dir():
        return []

    found: List[Clip] = []

    for vehicle_root in os.scandir(share):
        if not vehicle_root.is_dir():
            continue

        vehicle = detect_vehicle_from_folder(vehicle_root.name)
        event_map = FOLDER_EVENT_MAP.get(vehicle, {})

        try:
            subfolders = [e.name for e in os.scandir(vehicle_root.path) if e.is_dir()]
        except OSError:
            continue

        for folder in subfolders:
            folder_path = Path(vehicle_root.path) / folder
            if not folder_path.is_dir():
                continue

            event_type = event_map.get(folder, EventType.UNKNOWN)

            for file_path, relative in _walk_folder(folder_path, base_path):
                clip = _build_clip(file_path, folder, vehicle, event_type, base_path)
                if clip:
                    found.append(clip)

    return found


def _walk_folder(folder_path: Path, base_path: str):
    for root, _, files in os.walk(folder_path):
        root_path = Path(root)
        for fname in files:
            ext = Path(fname).suffix.lower()
            if ext not in VIDEO_EXTENSIONS:
                continue
            file_path = root_path / fname
            try:
                rel = str(file_path.relative_to(Path(base_path)))
            except ValueError:
                rel = str(file_path)
            yield file_path, rel


def _build_clip(
    file_path: Path,
    folder: str,
    vehicle: VehicleType,
    event_type: EventType,
    base_path: str,
) -> Optional[Clip]:
    try:
        stat = file_path.stat()
    except OSError:
        return None

    filename = file_path.name
    try:
        relative = str(file_path.relative_to(Path(base_path)))
    except ValueError:
        relative = str(file_path)

    timestamp = parse_timestamp(filename, folder, vehicle)
    camera_angle = parse_camera_angle(filename, vehicle)

    return Clip(
        id=clip_id(relative),
        filename=filename,
        relativePath=relative,
        folder=folder,
        vehicle=vehicle,
        eventType=event_type,
        cameraAngle=camera_angle,
        timestamp=timestamp,
        size=stat.st_size,
        sizeString=byte_count_fmt(stat.st_size),
        hasVideo=True,
    )