import io
import logging
from typing import Iterator, List, Optional

from .cloud_client import CloudClient

logger = logging.getLogger(__name__)

try:
    from pyicloud import PyiCloudService
    ICLOUD_AVAILABLE = True
except ImportError:
    ICLOUD_AVAILABLE = False
    PyiCloudService = None


class iCloudClient(CloudClient):
    provider_name = "icloud"

    def __init__(self, credentials: dict):
        self.username = credentials.get("username", "")
        self.password = credentials.get("password", "")
        self.folder_path = credentials.get("folder_path", "/Drivecam")
        self._client: Optional["PyiCloudService"] = None

    def _get_client(self) -> "PyiCloudService":
        if self._client is None:
            if not ICLOUD_AVAILABLE:
                raise ImportError("pyicloud not installed: pip install pyicloud")
            self._client = PyiCloudService(self.username, self.password)
        return self._client

    def get_auth_url(self, redirect_uri: str, state: str) -> str:
        return "https://support.apple.com/en-us/HT208674"

    def get_access_token(self) -> Optional[str]:
        try:
            client = self._get_client()
            return getattr(client, "session", None)
        except Exception:
            return None

    def test_connection(self) -> dict:
        try:
            if not ICLOUD_AVAILABLE:
                return {"success": False, "message": "pyicloud package not installed"}
            client = self._get_client()
            account = client.account_info()
            return {"success": True, "message": f"Connected as {account.get('name', self.username)}"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def list_files(self, path: str) -> List[dict]:
        try:
            client = self._get_client()
            if hasattr(client, "drive") and hasattr(client.drive, "ls"):
                items = client.drive.ls(path)
                results = []
                for item in items:
                    results.append({
                        "name": item.get("name", ""),
                        "path": item.get("filePath", path + "/" + item.get("name", "")),
                        "is_directory": item.get("type") == "FOLDER",
                        "size": item.get("size", 0),
                        "modified": item.get("modified", ""),
                    })
                return results
            return []
        except Exception as e:
            logger.error(f"iCloud list_files error: {e}")
            return []

    def get_file(self, path: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        try:
            client = self._get_client()
            if hasattr(client, "drive") and hasattr(client.drive, "download"):
                data = client.drive.download(path)
                buffer = io.BytesIO(data)
                while True:
                    chunk = buffer.read(chunk_size)
                    if not chunk:
                        break
                    yield chunk
        except Exception as e:
            logger.error(f"iCloud get_file error: {e}")

    def get_thumbnail(self, path: str) -> Optional[bytes]:
        try:
            client = self._get_client()
            if hasattr(client, "drive") and hasattr(client.drive, "thumbnail"):
                thumb = client.drive.thumbnail(path)
                if thumb:
                    return thumb
            return None
        except Exception as e:
            logger.error(f"iCloud thumbnail error: {e}")
            return None

    def scan_clips(self, folder_path: str) -> List[dict]:
        from ..scanner import VIDEO_EXTENSIONS, detect_vehicle_from_folder, FOLDER_EVENT_MAP
        from ..scanner import parse_timestamp, parse_camera_angle, byte_count_fmt, MIN_TIMESTAMP
        from ..scanner import _event_key
        from ..models import VehicleType, EventType, CameraAngle

        clips = []
        try:
            client = self._get_client()
            if not hasattr(client, "drive") or not hasattr(client.drive, "ls"):
                return clips

            def walk_folder(current_path: str, parent_event_type: EventType = EventType.UNKNOWN) -> List[dict]:
                folder_clips = []
                try:
                    items = client.drive.ls(current_path)
                except Exception:
                    return folder_clips

                folder_name = current_path.split("/")[-1] if current_path else ""
                vehicle = detect_vehicle_from_folder(folder_name)
                event_map = FOLDER_EVENT_MAP.get(vehicle, {})
                event_type = event_map.get(folder_name, parent_event_type)

                for item in items:
                    item_path = item.get("filePath", "")
                    is_dir = item.get("type") == "FOLDER"

                    if is_dir:
                        folder_clips.extend(walk_folder(item_path, event_type))
                    else:
                        name = item.get("name", "")
                        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                        if f".{ext}" not in VIDEO_EXTENSIONS:
                            continue

                        timestamp = parse_timestamp(name, folder_name, vehicle)
                        folder_clips.append({
                            "name": name,
                            "relative_path": item_path,
                            "folder": folder_name,
                            "vehicle": vehicle,
                            "event_type": event_type,
                            "camera_angle": parse_camera_angle(name, vehicle),
                            "timestamp": timestamp,
                            "event_key": _event_key(vehicle, timestamp),
                            "size": item.get("size", 0),
                            "size_string": byte_count_fmt(item.get("size", 0)),
                            "is_video": True,
                        })

                return folder_clips

            clips.extend(walk_folder(folder_path))

        except Exception as e:
            logger.error(f"iCloud scan_clips error: {e}")

        return clips