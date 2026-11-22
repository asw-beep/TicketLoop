"""S3-compatible object storage for private documents (M4).

A route that needs storage declares a parameter of type Storage:

    @router.post("/tickets/{ticket_id}/documents")
    def upload(ticket_id: UUID, storage: Storage, ...) -> ...: ...

The app builds one S3Storage at startup (app.state.storage); tests swap in InMemoryStorage.
"""

from typing import Annotated

from fastapi import Depends, Request

from app.infrastructure.storage.base import PRESIGN_SECONDS, ObjectStorage, StorageError
from app.infrastructure.storage.memory import InMemoryStorage
from app.infrastructure.storage.s3 import S3Storage


def get_storage(request: Request) -> ObjectStorage:
    return request.app.state.storage


Storage = Annotated[ObjectStorage, Depends(get_storage)]

__all__ = [
    "PRESIGN_SECONDS",
    "InMemoryStorage",
    "ObjectStorage",
    "S3Storage",
    "Storage",
    "StorageError",
    "get_storage",
]
