"""Default local backends: SQLite structured store + filesystem blob store.

Zero-setup — identical behaviour to the pre-storage-layer layout.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path

from src.storage.base import BlobStore, StructuredStore, validate_key, validate_table

# Logical table → dedicated SQLite file (preserves existing queue/event_bus paths).
_TABLE_DB = {
    "queue": "results/queue.db",
    "event_bus": "results/event_bus.db",
    "feedback_events": "results/feedback.db",
    "ragas_scores": "results/ragas_scores.db",
    "user_examples": "results/user_examples.db",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS records (
    id   TEXT PRIMARY KEY,
    data TEXT NOT NULL
);
"""


class LocalSQLiteStore(StructuredStore):
    def __init__(self, table_paths: dict[str, str] | None = None, root: str | Path = "."):
        self.root = Path(root)
        self.table_paths = {**_TABLE_DB, **(table_paths or {})}

    def _db_path(self, table: str) -> Path:
        validate_table(table)
        rel = self.table_paths.get(table, f"results/{table}.db")
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _conn(self, table: str) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path(table))
        conn.row_factory = sqlite3.Row
        conn.execute(_SCHEMA)
        return conn

    def insert(self, table: str, record: dict) -> str:
        validate_table(table)
        data = dict(record)
        rid = str(data.get("id") or uuid.uuid4())
        data["id"] = rid
        with self._conn(table) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO records (id, data) VALUES (?, ?)",
                (rid, json.dumps(data)),
            )
        return rid

    def get(self, table: str, record_id: str) -> dict | None:
        validate_table(table)
        with self._conn(table) as conn:
            row = conn.execute(
                "SELECT data FROM records WHERE id = ?", (record_id,)
            ).fetchone()
        return json.loads(row["data"]) if row else None

    def query(
        self,
        table: str,
        filters: dict | None = None,
        group_by: str | None = None,
        order_by: str | None = None,
        limit: int | None = None,
    ) -> list[dict]:
        validate_table(table)
        with self._conn(table) as conn:
            rows = conn.execute("SELECT data FROM records").fetchall()
        items = [json.loads(r["data"]) for r in rows]
        if filters:
            items = [i for i in items if all(i.get(k) == v for k, v in filters.items())]
        if group_by:
            # Return one representative per group (first seen); callers that need
            # aggregates should use reliability.py / count().
            seen: dict = {}
            for i in items:
                key = i.get(group_by)
                if key not in seen:
                    seen[key] = i
            items = list(seen.values())
        if order_by:
            desc = order_by.startswith("-")
            field = order_by[1:] if desc else order_by
            items.sort(key=lambda i: (i.get(field) is None, i.get(field)), reverse=desc)
        if limit is not None:
            items = items[: int(limit)]
        return items

    def update(self, table: str, record_id: str, patch: dict) -> None:
        validate_table(table)
        existing = self.get(table, record_id)
        if existing is None:
            raise KeyError(f"{table}/{record_id} not found")
        existing.update(patch)
        existing["id"] = record_id
        with self._conn(table) as conn:
            conn.execute(
                "UPDATE records SET data = ? WHERE id = ?",
                (json.dumps(existing), record_id),
            )

    def delete(self, table: str, record_id: str) -> None:
        validate_table(table)
        with self._conn(table) as conn:
            conn.execute("DELETE FROM records WHERE id = ?", (record_id,))

    def count(self, table: str, filters: dict | None = None) -> int:
        return len(self.query(table, filters=filters))

    def test_connection(self) -> None:
        with self._conn("queue") as conn:
            conn.execute("SELECT 1")


class LocalFileBlobStore(BlobStore):
    """Filesystem blob store rooted at the project directory (results/, data/, …)."""

    def __init__(self, root: str | Path = "."):
        self.root = Path(root)

    def _path(self, key: str) -> Path:
        validate_key(key)
        path = (self.root / key).resolve()
        root = self.root.resolve()
        if not str(path).startswith(str(root)):
            raise ValueError(f"blob key escapes root: {key!r}")
        return path

    def put(self, key: str, data: bytes | str, content_type: str | None = None) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = data.encode("utf-8") if isinstance(data, str) else data
        path.write_bytes(raw)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise FileNotFoundError(key)
        return path.read_bytes()

    def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def list(self, prefix: str = "") -> list[str]:
        if prefix:
            validate_key(prefix.rstrip("/"))
        root = self.root.resolve()
        base = (self.root / prefix).resolve() if prefix else root
        if not base.exists():
            return []
        out = []
        for p in base.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(root)).replace("\\", "/")
                out.append(rel)
        return sorted(out)

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    def test_connection(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)


def _demo() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        store = LocalSQLiteStore(root=tmp)
        rid = store.insert("feedback_events", {"label": "ACCEPTED_AS_IS", "category": "refund"})
        assert store.get("feedback_events", rid)["label"] == "ACCEPTED_AS_IS"
        assert store.count("feedback_events", {"label": "ACCEPTED_AS_IS"}) == 1
        store.update("feedback_events", rid, {"label": "EDITED_MINOR"})
        assert store.query("feedback_events", filters={"label": "EDITED_MINOR"})
        store.delete("feedback_events", rid)
        assert store.count("feedback_events") == 0

        blobs = LocalFileBlobStore(root=tmp)
        blobs.put("results/demo.json", '{"ok": true}')
        assert blobs.exists("results/demo.json")
        assert json.loads(blobs.get("results/demo.json"))["ok"] is True
        assert "results/demo.json" in blobs.list("results")
        blobs.delete("results/demo.json")
        assert not blobs.exists("results/demo.json")
    print("storage.local self-check OK")


if __name__ == "__main__":
    _demo()
