import os
from typing import Optional
from smbprotocol.connection import Connection
from smbprotocol.session import Session
from smbprotocol.tree import TreeConnect
from smbprotocol.open import Open, CreateDisposition, FileAttributes, ShareAccess, FilePipePrinterAccessMask
from smbprotocol.exceptions import SMBException


class SMBClient:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        share_path: str,
        domain: str = "",
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.domain = domain
        self.share_name = share_path.rstrip("/").split("/")[0] if share_path != "/" else "IPC$"
        self.share_subpath = "/".join(share_path.rstrip("/").split("/")[1:]) if share_path != "/" else ""
        self.conn = None
        self.session = None
        self.tree = None

    def connect(self) -> bool:
        try:
            self.conn = Connection(guid=os.urandom(16).hex(), server_name=self.host, port=self.port, require_signing=False)
            self.conn.connect()
            self.session = Session(self.conn, username=self.username, password=self.password, require_encryption=False)
            self.session.connect()
            self.tree = TreeConnect(self.session, self.share_name)
            self.tree.connect()
            return True
        except SMBException as e:
            raise Exception(f"SMB connection failed: {e}")
        except Exception as e:
            raise Exception(f"Connection error: {e}")

    def disconnect(self):
        if self.tree:
            try:
                self.tree.disconnect()
            except:
                pass
        if self.session:
            try:
                self.session.disconnect()
            except:
                pass
        if self.conn:
            try:
                self.conn.disconnect()
            except:
                pass

    def list_files(self, remote_dir: str = "") -> list:
        if not self.tree:
            raise Exception("Not connected. Call connect() first.")
        full_path = f"{self.share_subpath}/{remote_dir}".strip("/")
        try:
            dir_handle = Open(
                self.tree,
                path=full_path,
                desired_access=FilePipePrinterAccessMask.GENERIC_READ,
                file_attributes=FileAttributes.FILE_ATTRIBUTE_DIRECTORY,
                share_access=ShareAccess.FILE_SHARE_READ | ShareAccess.FILE_SHARE_WRITE | ShareAccess.FILE_SHARE_DELETE,
                create_disposition=CreateDisposition.FILE_OPEN,
            )
            dir_handle.create()
            files = []
            for entry in dir_handle.query_directory("*"):
                name = entry["file_name"].decode("utf-16-le", errors="ignore").rstrip("\0")
                if name in (".", ".."):
                    continue
                file_info = {
                    "name": name,
                    "is_directory": entry["file_attributes"] & FileAttributes.FILE_ATTRIBUTE_DIRECTORY != 0,
                    "size": entry["end_of_file"],
                }
                files.append(file_info)
            dir_handle.close()
            return files
        except SMBException as e:
            raise Exception(f"Failed to list directory: {e}")

    def download_file_range(self, remote_path: str, start: int = 0, end: Optional[int] = None) -> bytes:
        if not self.tree:
            raise Exception("Not connected.")
        full_path = f"{self.share_subpath}/{remote_path}".strip("/")
        try:
            file_handle = Open(
                self.tree,
                path=full_path,
                desired_access=FilePipePrinterAccessMask.GENERIC_READ,
                file_attributes=0,
                share_access=ShareAccess.FILE_SHARE_READ,
                create_disposition=CreateDisposition.FILE_OPEN,
            )
            file_handle.create()
            if end is None:
                length = file_handle.end_of_file - start
            else:
                length = end - start + 1
            data = file_handle.read(offset=start, length=length)
            file_handle.close()
            return data
        except SMBException as e:
            raise Exception(f"Failed to read file: {e}")

    def get_file_size(self, remote_path: str) -> int:
        if not self.tree:
            raise Exception("Not connected.")
        full_path = f"{self.share_subpath}/{remote_path}".strip("/")
        try:
            file_handle = Open(
                self.tree,
                path=full_path,
                desired_access=FilePipePrinterAccessMask.FILE_READ_ATTRIBUTES,
                file_attributes=0,
                share_access=ShareAccess.FILE_SHARE_READ,
                create_disposition=CreateDisposition.FILE_OPEN,
            )
            file_handle.create()
            size = file_handle.end_of_file
            file_handle.close()
            return size
        except SMBException as e:
            raise Exception(f"Failed to get file size: {e}")

    def test_connection(self) -> dict:
        try:
            self.connect()
            self.disconnect()
            return {"success": True, "message": "Connected successfully"}
        except Exception as e:
            return {"success": False, "message": str(e)}
