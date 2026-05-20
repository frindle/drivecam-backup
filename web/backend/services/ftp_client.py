import ftplib
from typing import Optional, Tuple


class FTPClient:
    def __init__(self, host: str, port: int, username: str, password: str):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.ftp = None

    def connect(self) -> bool:
        try:
            self.ftp = ftplib.FTP()
            self.ftp.connect(self.host, self.port, timeout=10)
            self.ftp.login(self.username, self.password)
            self.ftp.set_pasv(True)
            return True
        except ftplib.all_errors as e:
            raise Exception(f"FTP connection error: {e}")

    def disconnect(self):
        if self.ftp:
            try:
                self.ftp.quit()
            except ftplib.all_errors:
                try:
                    self.ftp.close()
                except:
                    pass
            finally:
                self.ftp = None

    def list_files(self, remote_dir: str = "") -> list:
        if not self.ftp:
            raise Exception("Not connected. Call connect() first.")
        try:
            files = []
            self.ftp.retrlines(f"LIST {remote_dir}" if remote_dir else "LIST", lambda line: files.append(self._parse_list_line(line)))
            return files
        except ftplib.error_perm as e:
            raise Exception(f"Failed to list directory: {e}")

    def _parse_list_line(self, line: str) -> dict:
        parts = line.split()
        if len(parts) < 8:
            return {"name": line, "is_directory": False, "size": 0}
        perms = parts[0]
        size = int(parts[4])
        name = " ".join(parts[8:])
        is_dir = perms[0] == "d"
        return {"name": name, "is_directory": is_dir, "size": size}

    def download_file_range(self, remote_path: str, start: int = 0, end: Optional[int] = None) -> bytes:
        if not self.ftp:
            raise Exception("Not connected.")
        try:
            length = (end - start + 1) if end is not None else None
            data = bytearray()
            def cb(chunk):
                data.extend(chunk)
            self.ftp.retrbinary(f"RETR {remote_path}", cb, rest=start)
            if length is not None:
                return bytes(data[:length])
            return bytes(data)
        except ftplib.error_perm as e:
            raise Exception(f"Failed to download file: {e}")

    def get_file_size(self, remote_path: str) -> int:
        if not self.ftp:
            raise Exception("Not connected.")
        try:
            size = self.ftp.size(remote_path)
            if size is not None:
                return size
        except (ftplib.error_perm, ftplib.error_temp, AttributeError):
            pass
        try:
            resp = self.ftp.sendcmd(f"MLST {remote_path}")
            import re
            m = re.search(r'Size=(\d+)', resp)
            if m:
                return int(m.group(1))
        except ftplib.all_errors:
            pass
        raise Exception("FTP server does not support file size queries")

    def test_connection(self, path: str = "") -> dict:
        connected = False
        try:
            connected = self.connect()
            if not connected:
                return {"success": False, "message": "Failed to connect"}
            try:
                if path:
                    self.ftp.cwd(path)
                return {"success": True, "message": f"Connected and can access path: {path or '/'}"}
            except ftplib.error_perm as e:
                return {"success": False, "message": f"Connected but cannot access path {path}: {e}"}
        except Exception as e:
            return {"success": False, "message": str(e)}
        finally:
            if connected:
                self.disconnect()
