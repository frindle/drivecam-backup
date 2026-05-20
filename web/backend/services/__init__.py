from .cloud_client import CloudClient
from .icloud_client import iCloudClient
from .gdrive_client import GoogleDriveClient
from .onedrive_client import OneDriveClient
from .dropbox_client import DropboxClient
from .smb_client import SMBClient
from .ftp_client import FTPClient
from .nfs_client import NFSClient


def get_cloud_client(provider: str, credentials: dict, folder_path: str = "") -> CloudClient:
    creds = dict(credentials)
    creds["folder_path"] = folder_path or creds.get("folder_path", "/Drivecam")
    if provider == "icloud":
        return iCloudClient(creds)
    elif provider == "gdrive":
        return GoogleDriveClient(creds)
    elif provider == "onedrive":
        return OneDriveClient(creds)
    elif provider == "dropbox":
        return DropboxClient(creds)
    raise ValueError(f"Unknown cloud provider: {provider}")