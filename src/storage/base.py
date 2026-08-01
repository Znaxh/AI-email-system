"""Abstract storage primitives: StructuredStore (queryable records) and BlobStore
(opaque documents). Company-agnostic — backends decide physical layout."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

_TABLE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_KEY_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/\-]{0,511}$")


def validate_table(table: str) -> str:
    if not _TABLE_RE.match(table or ""):
        raise ValueError(f"invalid logical table name: {table!r}")
    return table


def validate_key(key: str) -> str:
    if not key or ".." in key or key.startswith("/") or not _KEY_RE.match(key):
        raise ValueError(f"invalid blob key: {key!r}")
    return key


class StructuredStore(ABC):
    @abstractmethod
    def insert(self, table: str, record: dict) -> str:
        """Insert a record. Uses record['id'] when present; otherwise generates one."""

    @abstractmethod
    def get(self, table: str, record_id: str) -> dict | None: ...

    @abstractmethod
    def query(
        self,
        table: str,
        filters: dict | None = None,
        group_by: str | None = None,
        order_by: str | None = None,
        limit: int | None = None,
    ) -> list[dict]: ...

    @abstractmethod
    def update(self, table: str, record_id: str, patch: dict) -> None: ...

    @abstractmethod
    def delete(self, table: str, record_id: str) -> None: ...

    @abstractmethod
    def count(self, table: str, filters: dict | None = None) -> int: ...

    def test_connection(self) -> None:
        """Raise on failure. Default: no-op for backends that need no handshake."""
        return None


class BlobStore(ABC):
    @abstractmethod
    def put(self, key: str, data: bytes | str, content_type: str | None = None) -> None: ...

    @abstractmethod
    def get(self, key: str) -> bytes: ...

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def list(self, prefix: str = "") -> list[str]: ...

    @abstractmethod
    def delete(self, key: str) -> None: ...

    def test_connection(self) -> None:
        return None
