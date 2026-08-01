"""Storage factory — mirrors llm_client / email_source provider selection.

Bootstrap config (which backend) always comes from local config.json / .env —
never from the store itself.
"""

from __future__ import annotations

import os
from functools import lru_cache

from dotenv import load_dotenv

from src.config import load_config
from src.storage.base import BlobStore, StructuredStore

load_dotenv()


def _structured_provider(cfg: dict | None = None) -> str:
    cfg = cfg or load_config()
    return (
        os.getenv("STORAGE_STRUCTURED_PROVIDER")
        or cfg.get("storage_structured_provider")
        or "local"
    ).lower()


def _blob_provider(cfg: dict | None = None) -> str:
    cfg = cfg or load_config()
    return (
        os.getenv("STORAGE_BLOB_PROVIDER")
        or cfg.get("storage_blob_provider")
        or "local"
    ).lower()


@lru_cache(maxsize=4)
def _cached_structured(provider: str) -> StructuredStore:
    if provider == "local":
        from src.storage.local import LocalSQLiteStore

        return LocalSQLiteStore()
    if provider == "postgres":
        from src.storage.postgres import PostgresStore

        return PostgresStore()
    raise ValueError(f"Unknown storage_structured_provider: {provider!r}")


@lru_cache(maxsize=4)
def _cached_blob(provider: str) -> BlobStore:
    cfg = load_config()
    if provider == "local":
        from src.storage.local import LocalFileBlobStore

        return LocalFileBlobStore()
    if provider == "postgres":
        from src.storage.postgres import PostgresBlobStore

        return PostgresBlobStore()
    if provider == "s3":
        from src.storage.s3 import S3BlobStore

        return S3BlobStore(
            bucket=cfg.get("storage_s3_bucket") or None,
            config={
                "endpoint_url": cfg.get("storage_s3_endpoint_url") or None,
                "region": cfg.get("storage_s3_region") or None,
                "bucket": cfg.get("storage_s3_bucket") or None,
            },
        )
    if provider == "azure":
        from src.storage.azure_blob import AzureBlobStore

        return AzureBlobStore(container=cfg.get("storage_azure_container") or None)
    if provider == "gcs":
        from src.storage.gcs import GCSBlobStore

        return GCSBlobStore(bucket=cfg.get("storage_gcs_bucket") or None)
    raise ValueError(f"Unknown storage_blob_provider: {provider!r}")


def get_structured_store(cfg: dict | None = None) -> StructuredStore:
    return _cached_structured(_structured_provider(cfg))


def get_blob_store(cfg: dict | None = None) -> BlobStore:
    return _cached_blob(_blob_provider(cfg))


def clear_store_cache() -> None:
    _cached_structured.cache_clear()
    _cached_blob.cache_clear()


def migrate_local_to(target_structured: StructuredStore, target_blob: BlobStore) -> dict:
    """One-time copy of all local structured tables + operational blobs into targets."""
    from src.storage.local import LocalFileBlobStore, LocalSQLiteStore, _TABLE_DB

    src_s = LocalSQLiteStore()
    src_b = LocalFileBlobStore()
    stats = {"tables": {}, "blobs": 0}
    for table in _TABLE_DB:
        rows = src_s.query(table)
        for row in rows:
            target_structured.insert(table, row)
        stats["tables"][table] = len(rows)
    seen = set()
    for prefix in ("results", "policy_index", "data/user_examples.json", "policy"):
        for key in src_b.list(prefix if not prefix.endswith(".json") else "data"):
            if key in seen:
                continue
            if not (
                key.startswith("results/")
                or key.startswith("policy_index/")
                or key.startswith("policy/")
                or key == "data/user_examples.json"
            ):
                continue
            try:
                target_blob.put(key, src_b.get(key))
                seen.add(key)
                stats["blobs"] += 1
            except FileNotFoundError:
                pass
    return stats


def _demo() -> None:
    clear_store_cache()
    os.environ.pop("STORAGE_STRUCTURED_PROVIDER", None)
    os.environ.pop("STORAGE_BLOB_PROVIDER", None)
    s = get_structured_store()
    b = get_blob_store()
    assert type(s).__name__ == "LocalSQLiteStore"
    assert type(b).__name__ == "LocalFileBlobStore"
    s.test_connection()
    b.test_connection()
    print("storage.factory self-check OK")


if __name__ == "__main__":
    _demo()
