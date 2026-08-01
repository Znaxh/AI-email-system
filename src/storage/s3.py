"""S3-compatible blob store (AWS S3, MinIO, R2, Wasabi, GCS-via-HMAC). Lazy boto3."""

from __future__ import annotations

import os

from src.storage.base import BlobStore, validate_key


def _client(config: dict | None = None):
    try:
        import boto3
    except ImportError as exc:
        raise ImportError("S3 backend requires boto3. Install with: pip install boto3") from exc
    cfg = config or {}
    kwargs = {}
    endpoint = cfg.get("endpoint_url") or os.getenv("STORAGE_S3_ENDPOINT_URL")
    if endpoint:
        kwargs["endpoint_url"] = endpoint
    region = cfg.get("region") or os.getenv("STORAGE_S3_REGION") or os.getenv("AWS_DEFAULT_REGION")
    if region:
        kwargs["region_name"] = region
    key = os.getenv("STORAGE_S3_ACCESS_KEY") or os.getenv("AWS_ACCESS_KEY_ID")
    secret = os.getenv("STORAGE_S3_SECRET_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY")
    if key and secret:
        kwargs["aws_access_key_id"] = key
        kwargs["aws_secret_access_key"] = secret
    return boto3.client("s3", **kwargs)


class S3BlobStore(BlobStore):
    def __init__(self, bucket: str | None = None, config: dict | None = None):
        self.config = config or {}
        self.bucket = (
            bucket
            or self.config.get("bucket")
            or os.getenv("STORAGE_S3_BUCKET")
            or ""
        )
        if not self.bucket:
            raise ValueError("STORAGE_S3_BUCKET (or config bucket) is required for S3 store")

    def put(self, key: str, data: bytes | str, content_type: str | None = None) -> None:
        validate_key(key)
        raw = data.encode("utf-8") if isinstance(data, str) else data
        extra = {"ContentType": content_type} if content_type else {}
        _client(self.config).put_object(Bucket=self.bucket, Key=key, Body=raw, **extra)

    def get(self, key: str) -> bytes:
        validate_key(key)
        try:
            obj = _client(self.config).get_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            raise FileNotFoundError(key) from exc
        return obj["Body"].read()

    def exists(self, key: str) -> bool:
        validate_key(key)
        try:
            _client(self.config).head_object(Bucket=self.bucket, Key=key)
            return True
        except Exception:
            return False

    def list(self, prefix: str = "") -> list[str]:
        client = _client(self.config)
        keys: list[str] = []
        token = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            resp = client.list_objects_v2(**kwargs)
            for obj in resp.get("Contents") or []:
                keys.append(obj["Key"])
            if not resp.get("IsTruncated"):
                break
            token = resp.get("NextContinuationToken")
        return keys

    def delete(self, key: str) -> None:
        validate_key(key)
        _client(self.config).delete_object(Bucket=self.bucket, Key=key)

    def test_connection(self) -> None:
        _client(self.config).head_bucket(Bucket=self.bucket)
