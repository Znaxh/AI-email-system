"""Postgres structured store (+ optional bytea blob store). Lazy-imports psycopg."""

from __future__ import annotations

import json
import os
import uuid

from src.storage.base import BlobStore, StructuredStore, validate_key, validate_table

# Columns reliability / routing actually filter or group on.
_INDEXED = ("category", "cited_rule", "routing_decision", "timestamp", "label", "status", "decision")


def _connect(dsn: str | None = None):
    try:
        import psycopg
    except ImportError as exc:
        raise ImportError(
            "Postgres backend requires psycopg. Install with: pip install 'psycopg[binary]'"
        ) from exc
    dsn = dsn or os.getenv("STORAGE_POSTGRES_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        raise ValueError("STORAGE_POSTGRES_DSN (or DATABASE_URL) is required for Postgres store")
    return psycopg.connect(dsn)


class PostgresStore(StructuredStore):
    def __init__(self, dsn: str | None = None):
        self.dsn = dsn
        self._ensured: set[str] = set()

    def _ensure(self, conn, table: str) -> None:
        validate_table(table)
        if table in self._ensured:
            return
        cols = ", ".join(f"{c} TEXT" for c in _INDEXED)
        conn.execute(
            f"""CREATE TABLE IF NOT EXISTS {table} (
                id TEXT PRIMARY KEY,
                data JSONB NOT NULL,
                {cols}
            )"""
        )
        for c in _INDEXED:
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_{c} ON {table} ({c})"
            )
        self._ensured.add(table)

    def _row_vals(self, data: dict) -> dict:
        return {c: data.get(c) for c in _INDEXED}

    def insert(self, table: str, record: dict) -> str:
        validate_table(table)
        data = dict(record)
        rid = str(data.get("id") or uuid.uuid4())
        data["id"] = rid
        vals = self._row_vals(data)
        with _connect(self.dsn) as conn:
            self._ensure(conn, table)
            placeholders = ", ".join(["%s"] * (2 + len(_INDEXED)))
            col_names = ", ".join(["id", "data"] + list(_INDEXED))
            conn.execute(
                f"INSERT INTO {table} ({col_names}) VALUES ({placeholders}) "
                f"ON CONFLICT (id) DO UPDATE SET data = EXCLUDED.data, "
                + ", ".join(f"{c}=EXCLUDED.{c}" for c in _INDEXED),
                [rid, json.dumps(data)] + [vals[c] for c in _INDEXED],
            )
            conn.commit()
        return rid

    def get(self, table: str, record_id: str) -> dict | None:
        validate_table(table)
        with _connect(self.dsn) as conn:
            self._ensure(conn, table)
            row = conn.execute(
                f"SELECT data FROM {table} WHERE id = %s", (record_id,)
            ).fetchone()
        if not row:
            return None
        data = row[0]
        return data if isinstance(data, dict) else json.loads(data)

    def query(
        self,
        table: str,
        filters: dict | None = None,
        group_by: str | None = None,
        order_by: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        validate_table(table)
        clauses, params = [], []
        for k, v in (filters or {}).items():
            if k in _INDEXED:
                clauses.append(f"{k} = %s")
                params.append(v)
            else:
                clauses.append(f"data->>%s = %s")
                params.extend([k, str(v) if v is not None else None])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        order = ""
        if order_by:
            desc = order_by.startswith("-")
            field = order_by[1:] if desc else order_by
            direction = "DESC" if desc else "ASC"
            if field in _INDEXED:
                order = f"ORDER BY {field} {direction}"
            else:
                order = f"ORDER BY data->>%s {direction}"
                params.append(field)
        lim = f"LIMIT {int(limit)}" if limit is not None else ""
        sql = f"SELECT data FROM {table} {where} {order} {lim}"
        with _connect(self.dsn) as conn:
            self._ensure(conn, table)
            rows = conn.execute(sql, params).fetchall()
        items = [r[0] if isinstance(r[0], dict) else json.loads(r[0]) for r in rows]
        if group_by:
            seen: dict = {}
            for i in items:
                key = i.get(group_by)
                if key not in seen:
                    seen[key] = i
            items = list(seen.values())
        return items

    def update(self, table: str, record_id: str, patch: dict) -> None:
        existing = self.get(table, record_id)
        if existing is None:
            raise KeyError(f"{table}/{record_id} not found")
        existing.update(patch)
        existing["id"] = record_id
        self.insert(table, existing)

    def delete(self, table: str, record_id: str) -> None:
        validate_table(table)
        with _connect(self.dsn) as conn:
            self._ensure(conn, table)
            conn.execute(f"DELETE FROM {table} WHERE id = %s", (record_id,))
            conn.commit()

    def count(self, table: str, filters: dict | None = None) -> int:
        return len(self.query(table, filters=filters))

    def test_connection(self) -> None:
        with _connect(self.dsn) as conn:
            conn.execute("SELECT 1")


class PostgresBlobStore(BlobStore):
    """Optional single-connection blob store using bytea."""

    def __init__(self, dsn: str | None = None):
        self.dsn = dsn

    def _ensure(self, conn) -> None:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS blobs (
                key TEXT PRIMARY KEY,
                data BYTEA NOT NULL,
                content_type TEXT
            )"""
        )

    def put(self, key: str, data: bytes | str, content_type: str | None = None) -> None:
        validate_key(key)
        raw = data.encode("utf-8") if isinstance(data, str) else data
        with _connect(self.dsn) as conn:
            self._ensure(conn)
            conn.execute(
                """INSERT INTO blobs (key, data, content_type) VALUES (%s, %s, %s)
                   ON CONFLICT (key) DO UPDATE SET data = EXCLUDED.data,
                   content_type = EXCLUDED.content_type""",
                (key, raw, content_type),
            )
            conn.commit()

    def get(self, key: str) -> bytes:
        validate_key(key)
        with _connect(self.dsn) as conn:
            self._ensure(conn)
            row = conn.execute("SELECT data FROM blobs WHERE key = %s", (key,)).fetchone()
        if not row:
            raise FileNotFoundError(key)
        return bytes(row[0])

    def exists(self, key: str) -> bool:
        validate_key(key)
        with _connect(self.dsn) as conn:
            self._ensure(conn)
            row = conn.execute("SELECT 1 FROM blobs WHERE key = %s", (key,)).fetchone()
        return bool(row)

    def list(self, prefix: str = "") -> list[str]:
        with _connect(self.dsn) as conn:
            self._ensure(conn)
            if prefix:
                rows = conn.execute(
                    "SELECT key FROM blobs WHERE key LIKE %s ORDER BY key", (prefix + "%",)
                ).fetchall()
            else:
                rows = conn.execute("SELECT key FROM blobs ORDER BY key").fetchall()
        return [r[0] for r in rows]

    def delete(self, key: str) -> None:
        validate_key(key)
        with _connect(self.dsn) as conn:
            self._ensure(conn)
            conn.execute("DELETE FROM blobs WHERE key = %s", (key,))
            conn.commit()

    def test_connection(self) -> None:
        with _connect(self.dsn) as conn:
            conn.execute("SELECT 1")
