from abc import ABC, abstractmethod
from typing import Iterator, List, Optional


class CloudClient(ABC):
    provider_name: str = "generic"

    @abstractmethod
    def test_connection(self) -> dict:
        """Verify credentials work. Returns {success: bool, message: str}."""

    @abstractmethod
    def list_files(self, path: str) -> List[dict]:
        """List files in a cloud folder. Returns list of {name, path, is_directory, size, modified}."""

    @abstractmethod
    def get_file(self, path: str, chunk_size: int = 1024 * 1024) -> Iterator[bytes]:
        """Stream a file as bytes. Yield chunks."""

    @abstractmethod
    def get_thumbnail(self, path: str) -> Optional[bytes]:
        """Get thumbnail image bytes, or None if unavailable."""

    @abstractmethod
    def get_access_token(self) -> Optional[str]:
        """Return a valid access token, refreshing if needed."""

    @abstractmethod
    def get_auth_url(self, redirect_uri: str, state: str) -> str:
        """Return the OAuth2 authorization URL to redirect the user to."""