"""Object storage in a dict, for tests that check upload rules and need no real server."""

from app.infrastructure.storage.base import PRESIGN_SECONDS


class InMemoryStorage:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}  # key -> (data, content type)

    def put_object(self, key: str, data: bytes, content_type: str) -> None:
        self.objects[key] = (data, content_type)

    def presigned_get_url(
        self, key: str, content_type: str, expires_in: int = PRESIGN_SECONDS
    ) -> str:
        return f"memory://{key}?expires_in={expires_in}"

    def ensure_bucket(self) -> None:
        return None
