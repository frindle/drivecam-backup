import io
import logging
from typing import Iterator, List, Optional

from .cloud_client import CloudClient

logger = logging.getLogger(__name__)

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    GOOGLE_AVAILABLE = True
except ImportError:
    GOOGLE_AVAILABLE = False
    build = None
    HttpError = Exception


SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/drive.metadata.readonly",
]


class GoogleDriveClient(CloudClient):
    provider_name = "gdrive"
    DRIVE_API_VERSION = "v3"

    def __init__(self, credentials: dict):
        self.credentials_data = credentials.get("tokens", {})
        self.folder_path = credentials.get("folder_path", "/Drivecam")
        self._service = None

    def _get_service(self):
        if self._service is not None:
            return self._service

        if not GOOGLE_AVAILABLE:
            raise ImportError("google-api-python-client not installed: pip install google-api-python-client google-auth-oauthlib")

        creds = Credentials.from_authorized_user_info(self.credentials_data, SCOPES)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                raise Exception("Invalid credentials, need to re-authenticate")

        self._service = build("drive", self.DRIVE_API_VERSION, credentials=creds)
        return self._service

    def get_auth_url(self, redirect_uri: str, state: str) -> str:
        if not GOOGLE_AVAILABLE:
            raise ImportError("google-api-python-client not installed")
        flow = InstalledAppFlow.from_client_secrets_info(
            self.credentials_data.get("client_config", {}),
            SCOPES,
        )
        flow.redirect_uri = redirect_uri
        auth_url, _ = flow.authorization_url(prompt="consent", access_type="offline", state=state)
        return auth_url

    def get_access_token(self) -> Optional[str]:
        try:
            creds = Credentials.from_authorized_user_info(self.credentials_data, SCOPES)
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            return creds.token if creds else None
        except Exception:
            return None

    def _find_folder_id(self, path: str) -> Optional[str]:
        service = self._get_service()
        if not service:
            return None

        parts = [p for p in path.strip("/").split("/") if p]
        parent_id = "root"

        for part in parts:
            results = service.files().list(
                q=f"name=? and '{parent_id}' in parents and mimeType='application/vnd.google-apps.folder'",
                spaces="drive",
                fields="files(id, name)",
                pageSize=10,
            ).execute()
            files = results.get("files", [])
            matched = None
            for f in files:
                if f["name"] == part:
                    matched = f["id"]
                    break
            if not matched:
                return None
            parent_id = matched

        return parent_id

    def test_connection(self) -> dict:
        try:
            service = self._get_service()
            if not service:
                return {"success": False, "message": "Not authenticated"}
            about = service.about().get(fields="user").execute()
            return {"success": True, "message": f"Connected as {about.get('user', {}).get('emailAddress', 'Unknown')}"}
        except ImportError as e:
            return {"success": False, "message": str(e)}
        except Exception as e:
            return {"success": False, "message": str(e)}

    def list_files(self, path: str) -> List[dict]:
        try:
            service = self._get_service()
            if not service:
                return []

            folder_id = self._find_folder_id(path)
            if not folder_id:
                return []

            results = service.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                spaces="drive",
                fields="files(id, name, mimeType, size, modifiedTime)",
                pageSize=200,
            ).execute()
            files = results.get("files", [])
            return [
                {
                    "name": f["name"],
                    "path": f["id"],
                    "is_directory": f["mimeType"] == "application/vnd.google-apps.folder",
                    "size": int(f.get("size", 0)),
                    "modified": f.get("modifiedTime", ""),
                }
                for f in files
            ]
        except Exception as e:
            logger.error(f"Google Drive list_files error: {e}")
            return []

    def get_file(self, path: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        try:
            service = self._get_service()
            if not service:
                return

            request = service.files().get_media(fileId=path)
            from io import BytesIO
            buffer = BytesIO()
            downloader = io.BufferedReader(request.execute().raw)
            while True:
                chunk = downloader.read(chunk_size)
                if not chunk:
                    break
                yield chunk
        except Exception as e:
            logger.error(f"Google Drive get_file error: {e}")

    def get_thumbnail(self, path: str) -> Optional[bytes]:
        try:
            service = self._get_service()
            if not service:
                return None

            thumbnail = service.files().get_thumbnail(fileId=path).execute()
            return thumbnail.get("thumbnail")
        except Exception:
            return None

    def _scan_folder_recursive(self, folder_id: str, vehicle: str, event_map: dict, folder_name: str) -> List[dict]:
        from ..scanner import VIDEO_EXTENSIONS, detect_vehicle_from_folder
        from ..scanner import parse_timestamp, parse_camera_angle, byte_count_fmt
        from ..scanner import _event_key
        from ..models import EventType, CameraAngle

        clips = []
        try:
            service = self._get_service()
            if not service:
                return clips

            page_token = None
            while True:
                query = f"'{folder_id}' in parents and trashed=false"
                fields = "files(id, name, mimeType, size, modifiedTime)"
                if page_token:
                    results = service.files().list(
                        q=query, spaces="drive", fields=fields, pageSize=200, pageToken=page_token
                    ).execute()
                else:
                    results = service.files().list(
                        q=query, spaces="drive", fields=fields, pageSize=200
                    ).execute()

                for f in results.get("files", []):
                    name = f.get("name", "")
                    is_dir = f.get("mimeType") == "application/vnd.google-apps.folder"

                    if is_dir:
                        clips.extend(self._scan_folder_recursive(f.get("id"), vehicle, event_map, name))
                    else:
                        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                        if f".{ext}" not in VIDEO_EXTENSIONS:
                            continue

                        clip_vehicle = detect_vehicle_from_folder(folder_name)
                        event_type = event_map.get(folder_name, EventType.UNKNOWN)
                        timestamp = parse_timestamp(name, folder_name, clip_vehicle)
                        clips.append({
                            "name": name,
                            "relative_path": f.get("id"),
                            "folder": folder_name,
                            "vehicle": clip_vehicle,
                            "event_type": event_type,
                            "camera_angle": parse_camera_angle(name, clip_vehicle),
                            "timestamp": timestamp,
                            "event_key": _event_key(clip_vehicle, timestamp),
                            "size": int(f.get("size", 0)),
                            "size_string": byte_count_fmt(int(f.get("size", 0))),
                            "is_video": True,
                        })

                page_token = results.get("nextPageToken")
                if not page_token:
                    break

        except Exception as e:
            logger.error(f"Google Drive _scan_folder_recursive error: {e}")

        return clips

    def scan_clips(self, folder_path: str) -> List[dict]:
        from ..scanner import FOLDER_EVENT_MAP, detect_vehicle_from_folder

        clips = []
        try:
            service = self._get_service()
            if not service:
                return clips

            folder_id = self._find_folder_id(folder_path)
            if not folder_id:
                return clips

            folder_name = folder_path.strip("/").split("/")[-1]
            vehicle = detect_vehicle_from_folder(folder_name)
            event_map = FOLDER_EVENT_MAP.get(vehicle, {})
            clips = self._scan_folder_recursive(folder_id, vehicle.value, event_map, folder_name)

        except Exception as e:
            logger.error(f"Google Drive scan_clips error: {e}")

        return clips