"""Native Google Cloud Storage blob backend. Lazy-imports google-cloud-storage."""

from __future__ import annotations

import os

from src.storage.base import BlobStore, validate_key


def _client():
    try:
        from google.cloud import storage
    except ImportError as exc:
        raise ImportError(
            "GCS backend requires google-cloud-storage. "
            "Install with: pip install google-cloud-storage"
        ) from exc
    return storage.Client()


class GCSBlobStore(BlobStore):
    def __init__(self, bucket: str | None = None):
        self.bucket_name = bucket or os.getenv("STORAGE_GCS_BUCKET") or ""
        if not self.bucket_name:
            raise ValueError("STORAGE_GCS_BUCKET is required for GCS store")

    def _bucket(self):
        return _client().bucket(self.bucket_name)

    def put(self, key: str, data: bytes | str, content_type: str | None = None) -> None:
        validate_key(key)
        raw = data.encode("utf-8") if isinstance(data, str) else data
        blob = self._bucket().blob(key)
        blob.upload_from_string(raw, content_type=content_type)

    def get(self, key: str) -> bytes:
        validate_key(key)
        blob = self._bucket().blob(key)
        if not blob.exists():
            raise FileNotFoundError(key)
        return blob.download_as_bytes()

    def exists(self, key: str) -> bool:
        validate_key(key)
        return self._bucket().blob(key).exists()

    def list(self, prefix: str = "") -> list[str]:
        return [b.name for b in self._bucket().list_blobs(prefix=prefix)]

    def delete(self, key: str) -> None:
        validate_key(key)
        blob = self._bucket().blob(key)
        if blob.exists():
            blob.delete()

    def test_connection(self) -> None:
        self._bucket().exists()
