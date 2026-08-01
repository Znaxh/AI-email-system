"""Azure Blob storage backend. Lazy-imports azure-storage-blob."""

from __future__ import annotations

import os

from src.storage.base import BlobStore, validate_key


def _client(connection_string: str | None = None):
    try:
        from azure.storage.blob import BlobServiceClient
    except ImportError as exc:
        raise ImportError(
            "Azure backend requires azure-storage-blob. "
            "Install with: pip install azure-storage-blob"
        ) from exc
    cs = connection_string or os.getenv("STORAGE_AZURE_CONNECTION_STRING")
    if not cs:
        raise ValueError("STORAGE_AZURE_CONNECTION_STRING is required for Azure blob store")
    return BlobServiceClient.from_connection_string(cs)


class AzureBlobStore(BlobStore):
    def __init__(self, container: str | None = None, connection_string: str | None = None):
        self.connection_string = connection_string
        self.container = (
            container
            or os.getenv("STORAGE_AZURE_CONTAINER")
            or "app-data"
        )

    def _container(self):
        client = _client(self.connection_string)
        cc = client.get_container_client(self.container)
        try:
            cc.create_container()
        except Exception:
            pass
        return cc

    def put(self, key: str, data: bytes | str, content_type: str | None = None) -> None:
        validate_key(key)
        raw = data.encode("utf-8") if isinstance(data, str) else data
        kwargs = {}
        if content_type:
            kwargs["content_type"] = content_type
        self._container().upload_blob(name=key, data=raw, overwrite=True, **kwargs)

    def get(self, key: str) -> bytes:
        validate_key(key)
        try:
            return self._container().download_blob(key).readall()
        except Exception as exc:
            raise FileNotFoundError(key) from exc

    def exists(self, key: str) -> bool:
        validate_key(key)
        return self._container().get_blob_client(key).exists()

    def list(self, prefix: str = "") -> list[str]:
        return [b.name for b in self._container().list_blobs(name_starts_with=prefix)]

    def delete(self, key: str) -> None:
        validate_key(key)
        try:
            self._container().delete_blob(key)
        except Exception:
            pass

    def test_connection(self) -> None:
        self._container().get_container_properties()
