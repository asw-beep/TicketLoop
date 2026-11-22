"""What the rest of the app may ask of object storage, whichever server is behind it."""

from typing import Protocol

# How long a download link works. Short, because anyone holding the link can open the file.
PRESIGN_SECONDS = 300


class StorageError(Exception):
    """The storage server could not be reached or refused the request."""


class ObjectStorage(Protocol):
    def put_object(self, key: str, data: bytes, content_type: str) -> None:
        """Store `data` under `key`. Raises StorageError if the server fails."""
        ...

    def presigned_get_url(
        self, key: str, content_type: str, expires_in: int = PRESIGN_SECONDS
    ) -> str:
        """A link that opens the object for `expires_in` seconds, served as `content_type`."""
        ...

    def ensure_bucket(self) -> None:
        """Create the bucket if it is missing. Raises StorageError if the server fails."""
        ...
