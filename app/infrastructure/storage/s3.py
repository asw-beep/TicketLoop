"""Object storage over the S3 API with boto3. Works with MinIO, AWS S3 or any S3-compatible
server: only settings change, never this code.

The bucket is private. Nothing in it is readable without a signature, and the only way the
app hands a file out is a presigned GET link that expires after PRESIGN_SECONDS.
"""

from typing import Any, Self

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import Settings
from app.infrastructure.storage.base import PRESIGN_SECONDS, StorageError

# SigV4 is what every current S3 server expects. Path-style addressing
# (http://host/bucket/key) works on MinIO and on localhost, where a bucket subdomain
# (http://bucket.host/key) would not resolve.
_CONFIG = Config(
    signature_version="s3v4",
    s3={"addressing_style": "path"},
    connect_timeout=3,
    read_timeout=10,
    retries={"max_attempts": 2},
)


class S3Storage:
    def __init__(
        self,
        *,
        endpoint_url: str,
        public_url: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str,
    ) -> None:
        self.bucket = bucket
        credentials: dict[str, Any] = {
            "aws_access_key_id": access_key,
            "aws_secret_access_key": secret_key,
            "region_name": region,
            "config": _CONFIG,
        }
        # Two clients: one for real calls from the API to the server, one that only signs
        # links for the host the browser reaches. Signing is local maths, no network call.
        self._client = boto3.client("s3", endpoint_url=endpoint_url, **credentials)
        self._signer = boto3.client("s3", endpoint_url=public_url, **credentials)

    @classmethod
    def from_settings(cls, settings: Settings, bucket: str | None = None) -> Self:
        return cls(
            endpoint_url=settings.storage_endpoint_url,
            public_url=settings.storage_public_endpoint,
            bucket=bucket or settings.storage_bucket,
            access_key=settings.storage_access_key,
            secret_key=settings.storage_secret_key.get_secret_value(),
            region=settings.storage_region,
        )

    def put_object(self, key: str, data: bytes, content_type: str) -> None:
        try:
            self._client.put_object(
                Bucket=self.bucket, Key=key, Body=data, ContentType=content_type
            )
        except (BotoCoreError, ClientError) as error:
            raise StorageError(f"could not store object: {type(error).__name__}") from error

    def presigned_get_url(
        self, key: str, content_type: str, expires_in: int = PRESIGN_SECONDS
    ) -> str:
        return self._signer.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                # Served with the type we checked, and shown in the browser, not downloaded.
                "ResponseContentType": content_type,
                "ResponseContentDisposition": "inline",
            },
            ExpiresIn=expires_in,
        )

    def ensure_bucket(self) -> None:
        try:
            self._client.head_bucket(Bucket=self.bucket)
            return
        except ClientError as error:
            if error.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket"):
                raise StorageError(f"could not check bucket: {error}") from error
        except BotoCoreError as error:
            raise StorageError(f"could not reach storage: {type(error).__name__}") from error
        try:
            self._client.create_bucket(Bucket=self.bucket)
        except (BotoCoreError, ClientError) as error:
            raise StorageError(f"could not create bucket: {type(error).__name__}") from error

    def delete_all_objects(self) -> None:
        """Empty the bucket. For tests only, which reuse one bucket across runs."""
        try:
            pages = self._client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket)
            for page in pages:
                keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
                if keys:
                    self._client.delete_objects(Bucket=self.bucket, Delete={"Objects": keys})
        except (BotoCoreError, ClientError) as error:
            raise StorageError(f"could not empty bucket: {type(error).__name__}") from error
