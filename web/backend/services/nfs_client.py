import os
import stat
from typing import List, Optional


class NFSClient:
    def __init__(self, host: str, port: int, path: str):
        self.host = host
        self.port = port
        self.path = path.rstrip("/")
        self.mounted_at = None

    def connect(self) -> bool:
        import subprocess

        mount_point = f"/tmp/nfs_mount_{abs(hash(self.host + self.path))}"
        os.makedirs(mount_point, exist_ok=True)

        try:
            result = subprocess.run(
                ["mount", "-t", "nfs", "-o", f"port={self.port},ro", f"{self.host}:{self.path}", mount_point],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if result.returncode == 0:
                self.mounted_at = mount_point
                return True
            return False
        except subprocess.TimeoutExpired:
            return False
        except FileNotFoundError:
            raise Exception("mount command not available (nfs-common may not be installed)")
        except Exception as e:
            raise Exception(f"NFS mount error: {e}")

    def disconnect(self):
        if self.mounted_at:
            try:
                subprocess.run(["umount", self.mounted_at], capture_output=True, timeout=5)
            except:
                pass
            self.mounted_at = None

    def list_files(self, remote_dir: str = "") -> List[dict]:
        if not self.mounted_at:
            raise Exception("Not connected. Call connect() first.")
        full_path = os.path.join(self.mounted_at, remote_dir.lstrip("/"))
        if not os.path.isdir(full_path):
            return []
        files = []
        try:
            for entry in os.scandir(full_path):
                try:
                    is_dir = entry.is_dir()
                    size = entry.stat().st_size if not is_dir else 0
                    files.append({
                        "name": entry.name,
                        "is_directory": is_dir,
                        "size": size,
                    })
                except OSError:
                    continue
        except OSError:
            pass
        return files

    def download_file_range(self, remote_path: str, start: int = 0, end: Optional[int] = None) -> bytes:
        if not self.mounted_at:
            raise Exception("Not connected.")
        full_path = os.path.join(self.mounted_at, remote_path.lstrip("/"))
        try:
            with open(full_path, "rb") as f:
                f.seek(start)
                if end is not None:
                    length = end - start + 1
                    return f.read(length)
                return f.read()
        except OSError as e:
            raise Exception(f"Failed to read file: {e}")

    def get_file_size(self, remote_path: str) -> int:
        if not self.mounted_at:
            raise Exception("Not connected.")
        full_path = os.path.join(self.mounted_at, remote_path.lstrip("/"))
        try:
            return os.path.getsize(full_path)
        except OSError as e:
            raise Exception(f"Failed to get file size: {e}")

    def test_connection(self) -> dict:
        try:
            connected = self.connect()
            if connected:
                self.disconnect()
                return {"success": True, "message": "Connected successfully"}
            else:
                return {"success": False, "message": "NFS mount failed"}
        except Exception as e:
            return {"success": False, "message": str(e)}