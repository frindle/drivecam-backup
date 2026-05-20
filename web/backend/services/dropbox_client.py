import io
import logging
from typing import Iterator, List, Optional

from .cloud_client import CloudClient

logger = logging.getLogger(__name__)

try:
    import dropbox
    DROPBOX_AVAILABLE = True
except ImportError:
    DROPBOX_AVAILABLE = False
    dropbox = None


class DropboxClient(CloudClient):
    provider_name = "dropbox"

    def __init__(self, credentials: dict):
        self.app_key = credentials.get("app_key", "")
        self.app_secret = credentials.get("app_secret", "")
        self.access_token = credentials.get("access_token", "")
        self.refresh_token = credentials.get("refresh_token", "")
        self.folder_path = credentials.get("folder_path", "/Drivecam")
        self._client: Optional["dropbox.Dropbox"] = None

    def _get_client(self) -> "dropbox.Dropbox":
        if self._client is not None:
            return self._client
        if not DROPBOX_AVAILABLE:
            raise ImportError("dropbox not installed: pip install dropbox")
        if self.access_token:
            self._client = dropbox.Dropbox(
                oauth2_access_token=self.access_token,
                oauth2_refresh_token=self.refresh_token,
                app_key=self.app_key,
                app_secret=self.app_secret,
            )
        return self._client

    def get_auth_url(self, redirect_uri: str, state: str) -> str:
        if not DROPBOX_AVAILABLE:
            raise ImportError("dropbox not installed")
        return f"https://www.dropbox.com/oauth2/authorize?client_id={self.app_key}&response_type=code&redirect_uri={redirect_uri}&state={state}"

    def get_access_token(self) -> Optional[str]:
        return self.access_token or None

    def test_connection(self) -> dict:
        try:
            client = self._get_client()
            if not client:
                return {"success": False, "message": "Not authenticated"}
            account = client.users_get_current_account()
            return {"success": True, "message": f"Connected as {account.name.display_name}"}
        except ImportError as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def list_files(self, path: str) -> List[dict]:
        try:
            client = self._get_client()
            if not client:
                return []
            result = client.files_list_folder(path)
            items = []
            while True:
                for entry in result.entries:
                    items.append({
                        "name": entry.name,
                        "path": entry.path_lower,
                        "is_directory": isinstance(entry, dropbox.files.FolderMetadata),
                        "size": entry.size if hasattr(entry, "size") else 0,
                        "modified": getattr(entry, "server_modified", "") or "",
                    })
                if not result.has_more:
                    break
                result = client.files_list_folder_continue(result.cursor)
            return items
        except Exception as e:
            logger.error(f"Dropbox list_files error: {e}")
            return []

    def get_file(self, path: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        try:
            client = self._get_client()
            if not client:
                return
            _, resp = client.files_download(path)
            buffer = io.BytesIO(resp.content)
            while True:
                chunk = buffer.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        except Exception as e:
            logger.error(f"Dropbox get_file error: {e}")

    def get_thumbnail(self, path: str) -> Optional[bytes]:
        try:
            client = self._get_client()
            if not client:
                return None
            _, result = client.files_get_thumbnail(
                path,
                format=dropbox.files.ThumbnailFormat.JPEG,
                size=dropbox.files.ThumbnailSize.W256H256,
            )
            return result
        except Exception:
            return None

    def scan_clips(self, folder_path: str) -> List[dict]:
        from ..scanner import VIDEO_EXTENSIONS, detect_vehicle_from_folder, FOLDER_EVENT_MAP
        from ..scanner import parse_timestamp, parse_camera_angle, byte_count_fmt
        from ..scanner import _event_key
        from ..models import VehicleType, EventType, CameraAngle

        clips = []
        try:
            client = self._get_client()
            if not client:
                return clips

            def walk_folder(current_path: str, parent_event: EventType = EventType.UNKNOWN) -> List[dict]:
                folder_clips = []
                try:
                    result = client.files_list_folder(current_path)
                except Exception:
                    return folder_clips

                folder_name = current_path.split("/")[-1]
                vehicle = detect_vehicle_from_folder(folder_name)
                event_map = FOLDER_EVENT_MAP.get(vehicle, {})
                event_type = event_map.get(folder_name, parent_event)

                while True:
                    for entry in result.entries:
                        entry_path = entry.path_lower
                        if isinstance(entry, dropbox.files.FolderMetadata):
                            folder_clips.extend(walk_folder(entry_path, event_type))
                        else:
                            name = entry.name
                            ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                            if f".{ext}" not in VIDEO_EXTENSIONS:
                                continue
                            timestamp = parse_timestamp(name, folder_name, vehicle)
                            folder_clips.append({
                                "name": name,
                                "relative_path": entry_path,
                                "folder": folder_name,
                                "vehicle": vehicle,
                                "event_type": event_type,
                                "camera_angle": parse_camera_angle(name, vehicle),
                                "timestamp": timestamp,
                                "event_key": _event_key(vehicle, timestamp),
                                "size": entry.size if hasattr(entry, "size") else 0,
                                "size_string": byte_count_fmt(entry.size if hasattr(entry, "size") else 0),
                                "is_video": True,
                            })

                    if not result.has_more:
                        break
                    result = client.files_list_folder_continue(result.cursor)

                return folder_clips

            clips.extend(walk_folder(folder_path))

        except Exception as e:
            logger.error(f"Dropbox scan_clips error: {e}")

        return clips