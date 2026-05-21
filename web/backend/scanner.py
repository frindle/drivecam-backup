import hashlib
import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from .models import CameraAngle, Clip, EventType, VehicleType
from .services.smb_client import SMBClient
from .services.ftp_client import FTPClient
from .services.nfs_client import NFSClient

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".ts", ".mkv"}

FOLDER_EVENT_MAP = {
    VehicleType.TESLA: {
        "RecentClips": EventType.DRIVING,
        "SavedClips": EventType.MANUALLY_SAVED,
        "SentryClips": EventType.GEAR_GUARD,
    },
    VehicleType.RIVIAN: {
        "GearGuardVideo": EventType.GEAR_GUARD,
        "IncidentCam": EventType.MANUALLY_SAVED,
        "RoadCam": EventType.DRIVING,
    },
}

TESLA_FILENAME_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})-(front|back|left|right|cabin)\.mp4$",
    re.IGNORECASE,
)

RIVIAN_FILENAME_RE = re.compile(
    r"^(\d{2})_(\d{2})_(\d{2})_(\d{2})(\d{2})(\d{2})"
    r"(?:[_-](front-center|rear-center|side-left|side-right|front|rear|back|left|right|cabin|driver|passenger))?"
    r"\.(mp4|mov|ts|avi|mkv)$",
    re.IGNORECASE,
)


TESLA_ROOTS = {"teslacam"}
RIVIAN_ROOTS = {"rivian_dashcam"}
RIVIAN_EVENT_FOLDERS = {"gearguardvideo", "incidentcam", "roadcam"}


def detect_vehicle_from_folder(folder_name: str) -> VehicleType:
    fl = folder_name.lower()
    if fl in TESLA_ROOTS:
        return VehicleType.TESLA
    if fl in RIVIAN_ROOTS or fl in RIVIAN_EVENT_FOLDERS:
        return VehicleType.RIVIAN
    for root in TESLA_ROOTS:
        if fl.startswith(root + "_"):
            return VehicleType.TESLA
    for root in RIVIAN_ROOTS:
        if fl.startswith(root + "_"):
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
            month, day = int(m.group(1)), int(m.group(2))
            year = 2000 + int(m.group(3))
            hour, minute, second = int(m.group(4)), int(m.group(5)), int(m.group(6))
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
        if "rear" in fn or "back" in fn: return CameraAngle.BACK
        if "left" in fn: return CameraAngle.LEFT
        if "right" in fn: return CameraAngle.RIGHT
        if "cabin" in fn or "interior" in fn or "driver" in fn: return CameraAngle.CABIN
    return CameraAngle.UNKNOWN


def clip_id(relative_path: str, source: str = "local", share_id: Optional[int] = None) -> str:
     key = f"{source}:{share_id or 'local'}:{relative_path}"
     return hashlib.md5(key.encode()).hexdigest()[:12]


def byte_count_fmt(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


MIN_TIMESTAMP = datetime(1970, 1, 1)

def _event_key(vehicle: VehicleType, timestamp: Optional[datetime]) -> Optional[str]:
    if timestamp:
        return f"{vehicle.value}:{timestamp.strftime('%Y-%m-%d_%H-%M-%S')}"
    return None


def scan_folder(base_paths: List[str], clip_folders: Optional[List[str]] = None) -> List[Clip]:
    clips: List[Clip] = []
    for base_path in base_paths:
        clips.extend(_scan_single_path(base_path, clip_folders))
    clips.sort(key=lambda c: c.timestamp or MIN_TIMESTAMP, reverse=True)
    return clips


def _scan_single_path(base_path: str, clip_folders: Optional[List[str]] = None) -> List[Clip]:
    share = Path(base_path)
    if not share.is_dir():
        return []

    found: List[Clip] = []

    for vehicle_root in os.scandir(share):
        if not vehicle_root.is_dir():
            continue

        # Rivian flat SD card structure: GearGuardVideo / IncidentCam / RoadCam at data root
        if vehicle_root.name.lower() in RIVIAN_EVENT_FOLDERS:
            event_map = FOLDER_EVENT_MAP.get(VehicleType.RIVIAN, {})
            event_type = event_map.get(vehicle_root.name, EventType.UNKNOWN)
            for file_path, _ in _walk_folder(Path(vehicle_root.path), base_path):
                clip = _build_clip(file_path, vehicle_root.name, VehicleType.RIVIAN, event_type, base_path)
                if clip:
                    found.append(clip)
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
    source: str = "local",
    share_id: Optional[int] = None,
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

    cid = clip_id(relative, source, share_id)
    return Clip(
        id=cid,
        filename=filename,
        relativePath=relative,
        folder=folder,
        vehicle=vehicle,
        eventType=event_type,
        cameraAngle=camera_angle,
        timestamp=timestamp,
        eventKey=_event_key(vehicle, timestamp),
        size=stat.st_size,
        sizeString=byte_count_fmt(stat.st_size),
        hasVideo=True,
        source=source,
        share_id=share_id,
    )


def scan_remote_share(share_config: dict, clip_folders: Optional[List[str]] = None) -> List[Clip]:
    protocol = share_config.get("protocol", "").lower()
    share_id = share_config.get("id")

    if protocol == "smb":
        return _scan_smb_share(share_config, clip_folders)
    elif protocol == "ftp":
        return _scan_ftp_share(share_config, clip_folders)
    elif protocol == "nfs":
        return _scan_nfs_share(share_config, clip_folders)
    else:
        return []


def _scan_smb_share(share_config: dict, clip_folders: Optional[List[str]] = None) -> List[Clip]:
    clips = []
    host = share_config["host"]
    port = share_config.get("port") or 445
    username = share_config.get("username") or "guest"
    password = share_config.get("password") or ""
    path = share_config.get("path") or "/"
    share_id = share_config.get("id")

    try:
        client = SMBClient(
            host=host,
            port=port,
            username=username,
            password=password,
            share_path=path,
        )
        connected = client.connect()
        if not connected:
            return []

        found_files = _smb_walk(client, path)
        for file_info in found_files:
            if not file_info.get("is_video"):
                continue
            folder = file_info.get("folder", "Unknown")
            vehicle = file_info.get("vehicle", VehicleType.UNKNOWN)
            event_type = file_info.get("event_type", EventType.UNKNOWN)
            rel_path = file_info["relative_path"]

            clip = Clip(
                id=clip_id(rel_path, "smb", share_id),
                filename=file_info["name"],
                relativePath=rel_path,
                folder=folder,
                vehicle=vehicle,
                eventType=event_type,
                cameraAngle=file_info.get("camera_angle", CameraAngle.UNKNOWN),
                timestamp=file_info.get("timestamp"),
                eventKey=_event_key(vehicle, file_info.get("timestamp")),
                size=file_info.get("size", 0),
                sizeString=byte_count_fmt(file_info.get("size", 0)),
                hasVideo=True,
                source="smb",
                share_id=share_id,
            )
            clips.append(clip)

        client.disconnect()
    except Exception as e:
        print(f"SMB scan error for {host}: {e}")

    clips.sort(key=lambda c: c.timestamp or MIN_TIMESTAMP, reverse=True)
    return clips


def _smb_walk(client: SMBClient, base_path: str, parent_event: EventType = EventType.UNKNOWN) -> List[dict]:
    results = []
    try:
        entries = client.list_files(base_path)
    except OSError:
        return results

    for entry in entries:
        name = entry["name"]
        if name in (".", ".."):
            continue

        full_path = f"{base_path}/{name}".replace("//", "/")
        if entry["is_directory"]:
            vehicle = detect_vehicle_from_folder(name)
            if vehicle != VehicleType.UNKNOWN:
                event_map = FOLDER_EVENT_MAP.get(vehicle, {})
                for subentry in client.list_files(full_path):
                    subname = subentry["name"]
                    if subentry["is_directory"]:
                        subfolder_path = f"{full_path}/{subname}"
                        subfolder = subname
                        event_type = event_map.get(subfolder, EventType.UNKNOWN)
                        try:
                            for file_entry in client.list_files(subfolder_path):
                                if not file_entry["is_directory"]:
                                    ext = file_entry["name"].rsplit(".", 1)[-1].lower() if "." in file_entry["name"] else ""
                                    if f".{ext}" not in VIDEO_EXTENSIONS:
                                        continue
                                    rel = f"{subfolder}/{file_entry['name']}"
                                    results.append({
                                        "name": file_entry["name"],
                                        "relative_path": rel,
                                        "folder": subfolder,
                                        "vehicle": vehicle,
                                        "event_type": event_type,
                                        "camera_angle": parse_camera_angle(file_entry["name"], vehicle),
                                        "timestamp": parse_timestamp(file_entry["name"], subfolder, vehicle),
                                        "size": file_entry["size"],
                                        "is_video": True,
                                    })
                        except OSError:
                            pass
                    else:
                        ext = subname.rsplit(".", 1)[-1].lower() if "." in subname else ""
                        if f".{ext}" not in VIDEO_EXTENSIONS:
                            continue
                        event_type = event_map.get(name, EventType.UNKNOWN)
                        rel = f"{name}/{subname}"
                        results.append({
                            "name": subname,
                            "relative_path": rel,
                            "folder": name,
                            "vehicle": vehicle,
                            "event_type": event_type,
                            "camera_angle": parse_camera_angle(subname, vehicle),
                            "timestamp": parse_timestamp(subname, name, vehicle),
                            "size": subentry["size"],
                            "is_video": True,
                        })
            else:
                detected_event = event_map.get(name, EventType.UNKNOWN)
                inherited_event = detected_event if detected_event != EventType.UNKNOWN else parent_event
                results.extend(_smb_walk(client, full_path, inherited_event))

    return results


def _scan_nfs_share(share_config: dict, clip_folders: Optional[List[str]] = None) -> List[Clip]:
    clips = []
    host = share_config["host"]
    port = share_config.get("port") or 2049
    path = share_config.get("path") or "/"
    share_id = share_config.get("id")

    try:
        client = NFSClient(host=host, port=port, path=path)
        connected = client.connect()
        if not connected:
            return []

        found_files = _nfs_walk(client, path)
        for file_info in found_files:
            if not file_info.get("is_video"):
                continue
            folder = file_info.get("folder", "Unknown")
            vehicle = file_info.get("vehicle", VehicleType.UNKNOWN)
            event_type = file_info.get("event_type", EventType.UNKNOWN)
            rel_path = file_info["relative_path"]

            clip = Clip(
                id=clip_id(rel_path, "nfs", share_id),
                filename=file_info["name"],
                relativePath=rel_path,
                folder=folder,
                vehicle=vehicle,
                eventType=event_type,
                cameraAngle=file_info.get("camera_angle", CameraAngle.UNKNOWN),
                timestamp=file_info.get("timestamp"),
                eventKey=_event_key(vehicle, file_info.get("timestamp")),
                size=file_info.get("size", 0),
                sizeString=byte_count_fmt(file_info.get("size", 0)),
                hasVideo=True,
                source="nfs",
                share_id=share_id,
            )
            clips.append(clip)

        client.disconnect()
    except Exception as e:
        print(f"NFS scan error for {host}: {e}")

    clips.sort(key=lambda c: c.timestamp or MIN_TIMESTAMP, reverse=True)
    return clips


def _nfs_walk(client, base_path: str, parent_event: EventType = EventType.UNKNOWN) -> List[dict]:
    results = []
    try:
        entries = client.list_files(base_path)
    except OSError:
        return results

    for entry in entries:
        name = entry["name"]
        if name in (".", ".."):
            continue

        full_path = f"{base_path}/{name}".replace("//", "/")
        if entry["is_directory"]:
            vehicle = detect_vehicle_from_folder(name)
            if vehicle != VehicleType.UNKNOWN:
                event_map = FOLDER_EVENT_MAP.get(vehicle, {})
                for subentry in client.list_files(full_path):
                    if subentry["is_directory"]:
                        subfolder = subentry["name"]
                        event_type = event_map.get(subfolder, EventType.UNKNOWN)
                        try:
                            for file_entry in client.list_files(f"{full_path}/{subfolder}"):
                                if not file_entry["is_directory"]:
                                    ext = file_entry["name"].rsplit(".", 1)[-1].lower() if "." in file_entry["name"] else ""
                                    if f".{ext}" not in VIDEO_EXTENSIONS:
                                        continue
                                    rel = f"{subfolder}/{file_entry['name']}"
                                    results.append({
                                        "name": file_entry["name"],
                                        "relative_path": rel,
                                        "folder": subfolder,
                                        "vehicle": vehicle,
                                        "event_type": event_type,
                                        "camera_angle": parse_camera_angle(file_entry["name"], vehicle),
                                        "timestamp": parse_timestamp(file_entry["name"], subfolder, vehicle),
                                        "size": file_entry["size"],
                                        "is_video": True,
                                    })
                        except OSError:
                            pass
                    else:
                        ext = subentry["name"].rsplit(".", 1)[-1].lower() if "." in subentry["name"] else ""
                        if f".{ext}" not in VIDEO_EXTENSIONS:
                            continue
                        event_type = event_map.get(name, EventType.UNKNOWN)
                        rel = f"{name}/{subentry['name']}"
                        results.append({
                            "name": subentry["name"],
                            "relative_path": rel,
                            "folder": name,
                            "vehicle": vehicle,
                            "event_type": event_type,
                            "camera_angle": parse_camera_angle(subentry["name"], vehicle),
                            "timestamp": parse_timestamp(subentry["name"], name, vehicle),
                            "size": subentry["size"],
                            "is_video": True,
                        })
            else:
                detected_event = event_map.get(name, EventType.UNKNOWN)
                inherited_event = detected_event if detected_event != EventType.UNKNOWN else parent_event
                results.extend(_nfs_walk(client, full_path, inherited_event))

    return results


def _scan_ftp_share(share_config: dict, clip_folders: Optional[List[str]] = None) -> List[Clip]:
    clips = []
    host = share_config["host"]
    port = share_config.get("port") or 21
    username = share_config.get("username") or "anonymous"
    password = share_config.get("password") or ""
    path = share_config.get("path") or "/"
    share_id = share_config.get("id")

    try:
        client = FTPClient(host=host, port=port, username=username, password=password)
        connected = client.connect()
        if not connected:
            return []

        found_files = _ftp_walk(client, path)
        for file_info in found_files:
            if not file_info.get("is_video"):
                continue
            folder = file_info.get("folder", "Unknown")
            vehicle = file_info.get("vehicle", VehicleType.UNKNOWN)
            event_type = file_info.get("event_type", EventType.UNKNOWN)
            rel_path = file_info["relative_path"]

            clip = Clip(
                id=clip_id(rel_path, "ftp", share_id),
                filename=file_info["name"],
                relativePath=rel_path,
                folder=folder,
                vehicle=vehicle,
                eventType=event_type,
                cameraAngle=file_info.get("camera_angle", CameraAngle.UNKNOWN),
                timestamp=file_info.get("timestamp"),
                eventKey=_event_key(vehicle, file_info.get("timestamp")),
                size=file_info.get("size", 0),
                sizeString=byte_count_fmt(file_info.get("size", 0)),
                hasVideo=True,
                source="ftp",
                share_id=share_id,
            )
            clips.append(clip)

        client.disconnect()
    except Exception as e:
        print(f"FTP scan error for {host}: {e}")

    clips.sort(key=lambda c: c.timestamp or MIN_TIMESTAMP, reverse=True)
    return clips


def _ftp_walk(client: FTPClient, base_path: str, parent_event: EventType = EventType.UNKNOWN) -> List[dict]:
    results = []
    try:
        entries = client.list_files(base_path)
    except OSError:
        return results

    for entry in entries:
        name = entry["name"]
        if name in (".", ".."):
            continue

        full_path = f"{base_path}/{name}".replace("//", "/")
        if entry["is_directory"]:
            vehicle = detect_vehicle_from_folder(name)
            if vehicle != VehicleType.UNKNOWN:
                event_map = FOLDER_EVENT_MAP.get(vehicle, {})
                for subentry in client.list_files(full_path):
                    if subentry["is_directory"]:
                        subfolder = subentry["name"]
                        event_type = event_map.get(subfolder, EventType.UNKNOWN)
                        try:
                            for file_entry in client.list_files(f"{full_path}/{subfolder}"):
                                if not file_entry["is_directory"]:
                                    ext = file_entry["name"].rsplit(".", 1)[-1].lower() if "." in file_entry["name"] else ""
                                    if f".{ext}" not in VIDEO_EXTENSIONS:
                                        continue
                                    rel = f"{subfolder}/{file_entry['name']}"
                                    results.append({
                                        "name": file_entry["name"],
                                        "relative_path": rel,
                                        "folder": subfolder,
                                        "vehicle": vehicle,
                                        "event_type": event_type,
                                        "camera_angle": parse_camera_angle(file_entry["name"], vehicle),
                                        "timestamp": parse_timestamp(file_entry["name"], subfolder, vehicle),
                                        "size": file_entry["size"],
                                        "is_video": True,
                                    })
                        except OSError:
                            pass
                    else:
                        ext = subentry["name"].rsplit(".", 1)[-1].lower() if "." in subentry["name"] else ""
                        if f".{ext}" not in VIDEO_EXTENSIONS:
                            continue
                        event_type = event_map.get(name, EventType.UNKNOWN)
                        rel = f"{name}/{subentry['name']}"
                        results.append({
                            "name": subentry["name"],
                            "relative_path": rel,
                            "folder": name,
                            "vehicle": vehicle,
                            "event_type": event_type,
                            "camera_angle": parse_camera_angle(subentry["name"], vehicle),
                            "timestamp": parse_timestamp(subentry["name"], name, vehicle),
                            "size": subentry["size"],
                            "is_video": True,
                        })
            else:
                detected_event = event_map.get(name, EventType.UNKNOWN)
                inherited_event = detected_event if detected_event != EventType.UNKNOWN else parent_event
                results.extend(_ftp_walk(client, full_path, inherited_event))

    return results