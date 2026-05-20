import io
import logging
from datetime import datetime
from typing import Iterator, List, Optional

from .cloud_client import CloudClient

logger = logging.getLogger(__name__)

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


class OneDriveClient(CloudClient):
    provider_name = "onedrive"

    def __init__(self, credentials: dict):
        self.client_id = credentials.get("client_id", "")
        self.client_secret = credentials.get("client_secret", "")
        self.tenant_id = credentials.get("tenant_id", "common")
        self.access_token = credentials.get("access_token", "")
        self.refresh_token = credentials.get("refresh_token", "")
        self.folder_path = credentials.get("folder_path", "/Drivecam")
        self.drive_type = credentials.get("drive_type", "personal")
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        if not REQUESTS_AVAILABLE:
            raise ImportError("requests not installed")
        return None

    def get_auth_url(self, redirect_uri: str, state: str) -> str:
        base = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/authorize"
        params = (
            f"?client_id={self.client_id}"
            f"&response_type=code"
            f"&redirect_uri={redirect_uri}"
            f"&scope=Files.Read.All offline_access"
            f"&state={state}"
            f"&response_mode=query"
        )
        return base + params

    def get_access_token(self) -> Optional[str]:
        return self.access_token or None

    def _refresh_access_token(self) -> bool:
        try:
            if not REQUESTS_AVAILABLE:
                return False
            token_url = f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token"
            data = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
                "scope": "Files.Read.All offline_access",
            }
            resp = requests.post(token_url, data=data, timeout=30)
            if resp.ok:
                tokens = resp.json()
                self.access_token = tokens.get("access_token", "")
                self.refresh_token = tokens.get("refresh_token", self.refresh_token)
                return True
            return False
        except Exception as e:
            logger.error(f"OneDrive token refresh error: {e}")
            return False

    def test_connection(self) -> dict:
        try:
            if not REQUESTS_AVAILABLE:
                return {"success": False, "message": "requests not installed"}
            return {"success": True, "message": "OneDrive connection configured"}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def list_files(self, path: str) -> List[dict]:
        try:
            if not self.access_token:
                return []
            headers = {"Authorization": f"Bearer {self.access_token}"}
            encoded_path = path.replace(" ", "%20")
            drive_url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{encoded_path}:/children"
            results = []
            while drive_url:
                resp = requests.get(drive_url, headers=headers, timeout=30)
                if not resp.ok:
                    if resp.status_code == 401 and self._refresh_access_token():
                        headers["Authorization"] = f"Bearer {self.access_token}"
                        resp = requests.get(drive_url, headers=headers, timeout=30)
                    if not resp.ok:
                        break
                data = resp.json()
                for item in data.get("value", []):
                    results.append({
                        "name": item.get("name", ""),
                        "path": item.get("id", ""),
                        "is_directory": item.get("folder") is not None,
                        "size": item.get("size", 0),
                        "modified": item.get("lastModifiedDateTime", ""),
                    })
                drive_url = data.get("@odata.nextLink")
            return results
        except Exception as e:
            logger.error(f"OneDrive list_files error: {e}")
            return []

    def get_file(self, path: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        try:
            if not self.access_token:
                return
            headers = {"Authorization": f"Bearer {self.access_token}"}
            download_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{path}/content"
            resp = requests.get(download_url, headers=headers, stream=True, timeout=60)
            if resp.ok:
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if chunk:
                        yield chunk
            elif resp.status_code == 401 and self._refresh_access_token():
                headers["Authorization"] = f"Bearer {self.access_token}"
                resp = requests.get(download_url, headers=headers, stream=True, timeout=60)
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if chunk:
                        yield chunk
        except Exception as e:
            logger.error(f"OneDrive get_file error: {e}")

    def get_thumbnail(self, path: str) -> Optional[bytes]:
        try:
            if not self.access_token:
                return None
            headers = {"Authorization": f"Bearer {self.access_token}"}
            thumb_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{path}/thumbnails/0/custom"
            resp = requests.get(thumb_url, headers=headers, timeout=30)
            if resp.ok:
                data = resp.json()
                url = data.get("url")
                if url:
                    img_resp = requests.get(url, timeout=30)
                    if img_resp.ok:
                        return img_resp.content
            return None
        except Exception:
            return None

    def scan_clips(self, folder_path: str) -> List[dict]:
        from ..scanner import VIDEO_EXTENSIONS, detect_vehicle_from_folder, FOLDER_EVENT_MAP
        from ..scanner import parse_timestamp, parse_camera_angle, byte_count_fmt
        from ..scanner import _event_key
        from ..models import VehicleType, EventType, CameraAngle

        clips = []
        try:
            headers = {"Authorization": f"Bearer {self.access_token}"}
            encoded_path = folder_path.strip("/").replace(" ", "%20")
            drive_url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{encoded_path}:/children"
            folder_name = folder_path.split("/")[-1]
            vehicle = detect_vehicle_from_folder(folder_name)
            event_map = FOLDER_EVENT_MAP.get(vehicle, {})
            event_type = event_map.get(folder_name, EventType.UNKNOWN)

            while drive_url:
                resp = requests.get(drive_url, headers=headers, timeout=30)
                if not resp.ok:
                    if resp.status_code == 401 and self._refresh_access_token():
                        headers["Authorization"] = f"Bearer {self.access_token}"
                        resp = requests.get(drive_url, headers=headers, timeout=30)
                    if not resp.ok:
                        break
                data = resp.json()

                for item in data.get("value", []):
                    name = item.get("name", "")
                    is_dir = item.get("folder") is not None
                    if is_dir:
                        subfolder_path = folder_path.rstrip("/") + "/" + name
                        clips.extend(self.scan_clips(subfolder_path))
                        continue

                    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                    if f".{ext}" not in VIDEO_EXTENSIONS:
                        continue

                    timestamp = parse_timestamp(name, folder_name, vehicle)
                    clips.append({
                        "name": name,
                        "relative_path": item.get("id", ""),
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

                drive_url = data.get("@odata.nextLink")

        except Exception as e:
            logger.error(f"OneDrive scan_clips error: {e}")

        return clips