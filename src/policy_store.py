"""Hybrid policy store: ingest any supported policy document, cache structured
rules by section content-hash, and retrieve with BM25 + embeddings + RRF
(optional cross-encoder rerank).

Nothing here knows a specific company — swap the file under data/ and it works.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import numpy as np

from src.config import load_config
from src.policy_ingest import extract_sections, normalize_sections
from src.schema import PolicyRule

INDEX_DIR = Path("results/policy_index")


def rrf_fuse(rankings: list[list[str]], k: int = 60) -> list[str]:
    """Reciprocal Rank Fusion over ranked id lists. Deterministic on ties by id."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, doc_id in enumerate(ranking, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.keys(), key=lambda d: (-scores[d], d))


class PolicyStore:
    def __init__(
        self,
        path: str,
        *,
        index_dir: str | Path | None = None,
        config: dict | None = None,
    ):
        self.path = str(path)
        self.index_dir = Path(index_dir or INDEX_DIR)
        self.config = {**load_config(), **(config or {})}
        self.rules: list[PolicyRule] = []
        self.chunks: list[str] = []  # backward-compat for Settings preview
        self._bm25 = None
        self._bm25_tokens: list[list[str]] = []
        self._embeddings: np.ndarray | None = None
        self._embedder = None
        self._cross_encoder = None
        self.last_ingest_stats: dict = {}
        self._load_or_build()

    # ------------------------------------------------------------------ public

    def categories(self) -> list[str]:
        cats = sorted({r.category for r in self.rules if r.category and r.category != "global"})
        return cats

    def all_text(self) -> str:
        return "\n\n".join(r.text for r in self.rules)

    def rule_by_id(self, rule_id: str) -> PolicyRule | None:
        rid = rule_id.strip().upper()
        for r in self.rules:
            if r.id.upper() == rid:
                return r
        return None

    def retrieve(
        self,
        query: str,
        k: int = 3,
        *,
        category: str | None = None,
        region: str | None = None,
        as_of: str | None = None,
    ) -> list[str]:
        """Return top-k rule texts (backward-compatible)."""
        hits = self.retrieve_rules(query, k=k, category=category, region=region, as_of=as_of)
        return [r.text for r in hits]

    def retrieve_rules(
        self,
        query: str,
        k: int = 4,
        *,
        category: str | None = None,
        region: str | None = None,
        as_of: str | None = None,
    ) -> list[PolicyRule]:
        candidates = self._filter(category=category, region=region, as_of=as_of)
        # If scoping left only globals (or nothing), search the full corpus so
        # a mistyped/unknown category still retrieves real rules.
        non_global = [r for r in candidates if r.category != "global"]
        if not candidates or (category and category != "global" and not non_global):
            candidates = list(self.rules)
        if not candidates:
            return []

        cand_ids = [r.id for r in candidates]
        id_to_rule = {r.id: r for r in candidates}

        bm25_ranking = self._bm25_rank(query, candidates)
        emb_ranking = self._embedding_rank(query, candidates)
        fused = rrf_fuse(
            [bm25_ranking, emb_ranking] if emb_ranking else [bm25_ranking],
            k=int(self.config.get("rrf_k", 60)),
        )
        # Keep only candidates (safety)
        fused = [i for i in fused if i in id_to_rule]

        top_n = fused[: max(k * 3, k)]
        if self.config.get("cross_encoder_rerank") and len(top_n) > 1:
            top_n = self._rerank(query, top_n, id_to_rule)[:k]
        else:
            top_n = top_n[:k]
        return [id_to_rule[i] for i in top_n if i in id_to_rule]

    # ------------------------------------------------------------------ ingest

    def _blob(self):
        from src.storage.factory import get_blob_store

        return get_blob_store()

    def _index_key(self, name: str) -> str:
        # Version/hash-namespaced layout: policy_index/{namespace}/{name}
        # Falls back to legacy flat policy_index/{name} when no namespace is set
        # (only for reading during migration; writes always use a namespace when available).
        ns = (self.config.get("policy_index_namespace") or "").strip()
        if not ns:
            # Derive from file hash when path exists so activations don't collide.
            path = Path(self.path)
            if path.exists():
                ns = hashlib.sha256(path.read_bytes()).hexdigest()[:32]
                self.config["policy_index_namespace"] = ns
            else:
                return f"policy_index/{name}"
        return f"policy_index/{ns}/{name}"

    def _blob_read_json(self, name: str) -> dict | list | None:
        key = self._index_key(name)
        try:
            raw = self._blob().get(key)
            return json.loads(raw.decode("utf-8"))
        except FileNotFoundError:
            return None
        except Exception:
            return None

    def _blob_write_json(self, name: str, obj) -> None:
        self._blob().put(self._index_key(name), json.dumps(obj, indent=2), "application/json")

    def _blob_read_npy(self, name: str) -> np.ndarray | None:
        import io

        key = self._index_key(name)
        try:
            raw = self._blob().get(key)
            return np.load(io.BytesIO(raw))
        except FileNotFoundError:
            return None
        except Exception:
            return None

    def _blob_write_npy(self, name: str, arr: np.ndarray) -> None:
        import io

        buf = io.BytesIO()
        np.save(buf, arr)
        self._blob().put(self._index_key(name), buf.getvalue(), "application/octet-stream")

    def _load_or_build(self) -> None:
        # Keep a local mirror dir for tooling that still expects a path; BlobStore
        # is the source of truth for meta/rules/embeddings.
        self.index_dir.mkdir(parents=True, exist_ok=True)
        path = Path(self.path)
        if not path.exists():
            self.rules = []
            self.chunks = []
            self.last_ingest_stats = {"error": "missing_file", "path": self.path}
            return

        file_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        cached_meta = self._blob_read_json("meta.json") or {}
        if not isinstance(cached_meta, dict):
            cached_meta = {}

        use_llm = bool(self.config.get("policy_llm_chunking", False))
        sections = extract_sections(path, use_llm_fallback=use_llm)
        section_hashes = {s.content_hash: s for s in sections}
        current_hashes = set(section_hashes)

        cached_rules: list[PolicyRule] = []
        cached_by_hash: dict[str, PolicyRule] = {}
        raw_rules = self._blob_read_json("rules.json")
        if isinstance(raw_rules, list):
            try:
                cached_rules = [PolicyRule(**r) for r in raw_rules]
                cached_by_hash = {r.section_hash: r for r in cached_rules if r.section_hash}
            except Exception:
                cached_rules, cached_by_hash = [], {}

        reused, changed = [], []
        for sec in sections:
            if sec.content_hash in cached_by_hash:
                reused.append(cached_by_hash[sec.content_hash])
            else:
                changed.append(sec)

        deleted = [r for r in cached_rules if r.section_hash and r.section_hash not in current_hashes]

        new_rules = normalize_sections(
            changed,
            changed_hashes={s.content_hash for s in changed} if use_llm else set(),
            use_llm_for_changed=use_llm,
        )
        self.rules = reused + new_rules
        by_hash = {r.section_hash: r for r in self.rules}
        ordered = []
        for sec in sections:
            if sec.content_hash in by_hash:
                ordered.append(by_hash[sec.content_hash])
        self.rules = ordered or self.rules
        self.chunks = [r.text for r in self.rules]

        old_emb = None
        old_hash_order: list[str] = []
        if cached_meta.get("embedding_model") == self._embedding_model_name():
            old_emb = self._blob_read_npy("embeddings.npy")
            old_hash_order = list(cached_meta.get("section_hashes") or [])

        hash_to_vec: dict[str, np.ndarray] = {}
        if old_emb is not None and len(old_hash_order) == len(old_emb):
            for h, vec in zip(old_hash_order, old_emb):
                hash_to_vec[h] = vec

        need_embed = [r for r in self.rules if r.section_hash not in hash_to_vec]
        if need_embed and self.config.get("use_embeddings", True):
            new_vecs = self._embed_texts([r.text for r in need_embed])
            for r, vec in zip(need_embed, new_vecs):
                hash_to_vec[r.section_hash] = vec

        if self.rules and self.config.get("use_embeddings", True):
            self._embeddings = np.vstack(
                [hash_to_vec[r.section_hash] for r in self.rules]
            ).astype(np.float32)
        else:
            self._embeddings = None

        self._fit_bm25()

        meta = {
            "file_hash": file_hash,
            "path": self.path,
            "embedding_model": self._embedding_model_name(),
            "section_hashes": [r.section_hash for r in self.rules],
            "rule_ids": [r.id for r in self.rules],
            "categories": self.categories(),
        }
        self._blob_write_json("rules.json", [r.model_dump() for r in self.rules])
        self._blob_write_json("meta.json", meta)
        if self._embeddings is not None:
            self._blob_write_npy("embeddings.npy", self._embeddings)

        self.last_ingest_stats = {
            "path": self.path,
            "sections": len(sections),
            "rules": len(self.rules),
            "reused": len(reused),
            "reprocessed": len(changed),
            "deleted": len(deleted),
            "categories": self.categories(),
        }

    def _fit_bm25(self) -> None:
        self._bm25_tokens = [_tokenize(r.text) for r in self.rules]
        if not self._bm25_tokens:
            self._bm25 = None
            return
        try:
            from rank_bm25 import BM25Okapi

            self._bm25 = BM25Okapi(self._bm25_tokens)
        except Exception:
            self._bm25 = None

    # ------------------------------------------------------------------ rankers

    def _filter(
        self,
        *,
        category: str | None,
        region: str | None,
        as_of: str | None,
    ) -> list[PolicyRule]:
        out = []
        cat = (category or "").strip().lower()
        region = (region or "").strip().lower()
        for r in self.rules:
            # Always include global / cross-cutting rules.
            if cat and r.category not in ("global", cat) and r.category != "":
                # Also allow if category loosely matches heading keywords already baked in.
                if r.category != cat:
                    continue
            if region and r.region and r.region.lower() != region:
                continue
            if as_of and r.effective_date and r.effective_date > as_of:
                continue
            out.append(r)
        return out

    def _bm25_rank(self, query: str, candidates: list[PolicyRule]) -> list[str]:
        if not candidates:
            return []
        if self._bm25 is None or not self.rules:
            # Fallback: simple term overlap ranking
            q = set(_tokenize(query))
            scored = []
            for r in candidates:
                overlap = len(q & set(_tokenize(r.text)))
                scored.append((overlap, r.id))
            scored.sort(key=lambda x: (-x[0], x[1]))
            return [i for _, i in scored]

        # Score only candidates; BM25 is fit on full corpus — map by index.
        id_to_idx = {r.id: i for i, r in enumerate(self.rules)}
        scores = self._bm25.get_scores(_tokenize(query))
        ranked = sorted(
            candidates,
            key=lambda r: (-float(scores[id_to_idx[r.id]]), r.id)
            if r.id in id_to_idx
            else (0.0, r.id),
        )
        return [r.id for r in ranked]

    def _embedding_rank(self, query: str, candidates: list[PolicyRule]) -> list[str]:
        if not self.config.get("use_embeddings", True):
            return []
        if self._embeddings is None or not self.rules:
            return []
        q = self._embed_texts([query])[0]
        id_to_idx = {r.id: i for i, r in enumerate(self.rules)}
        scored = []
        for r in candidates:
            idx = id_to_idx.get(r.id)
            if idx is None:
                continue
            vec = self._embeddings[idx]
            sim = float(np.dot(q, vec) / (np.linalg.norm(q) * np.linalg.norm(vec) + 1e-9))
            scored.append((sim, r.id))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [i for _, i in scored]

    def _rerank(self, query: str, ids: list[str], id_to_rule: dict[str, PolicyRule]) -> list[str]:
        try:
            if self._cross_encoder is None:
                from sentence_transformers import CrossEncoder

                model_name = self.config.get(
                    "cross_encoder_model", "cross-encoder/ms-marco-MiniLM-L-6-v2"
                )
                self._cross_encoder = CrossEncoder(model_name)
            pairs = [(query, id_to_rule[i].text) for i in ids if i in id_to_rule]
            keep_ids = [i for i in ids if i in id_to_rule]
            scores = self._cross_encoder.predict(pairs)
            ranked = sorted(zip(keep_ids, scores), key=lambda x: (-float(x[1]), x[0]))
            return [i for i, _ in ranked]
        except Exception:
            return ids

    def _embed_texts(self, texts: list[str]) -> list[np.ndarray]:
        if not texts:
            return []
        # Offline / mock: deterministic hashing embeddings so pipeline works without downloads.
        if os.getenv("LLM_PROVIDER", "").lower() == "mock" or self.config.get("embedding_backend") == "hash":
            return [_hash_embed(t) for t in texts]
        try:
            if self._embedder is None:
                from sentence_transformers import SentenceTransformer

                model_name = self._embedding_model_name()
                self._embedder = SentenceTransformer(model_name)
            vecs = self._embedder.encode(texts, normalize_embeddings=True)
            return [np.asarray(v, dtype=np.float32) for v in vecs]
        except Exception:
            # Graceful degrade — still allow BM25-only retrieval.
            return [_hash_embed(t) for t in texts]

    def _embedding_model_name(self) -> str:
        return str(
            self.config.get("embedding_model")
            or os.getenv("EMBEDDING_MODEL")
            or "sentence-transformers/all-MiniLM-L6-v2"
        )


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _hash_embed(text: str, dim: int = 384) -> np.ndarray:
    """Deterministic bag-of-hashes vector for offline tests (no model download)."""
    v = np.zeros(dim, dtype=np.float32)
    for tok in _tokenize(text):
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        v[h % dim] += 1.0
    n = np.linalg.norm(v)
    if n > 0:
        v /= n
    return v


def resolve_policy_path(data_dir: str | Path = "data", config: dict | None = None) -> Path:
    """Pick the configured policy filename under data/, defaulting to policy.pdf."""
    cfg = {**load_config(), **(config or {})}
    name = cfg.get("policy_filename") or "policy.pdf"
    return Path(data_dir) / name


def _demo() -> None:
    # RRF determinism
    a = ["R1", "R2", "R3"]
    b = ["R2", "R1", "R4"]
    fused = rrf_fuse([a, b], k=60)
    assert fused[0] in ("R1", "R2")
    assert "R4" in fused

    # Ingest the real policy with hash embeddings (no network).
    store = PolicyStore(
        "data/policy.pdf",
        config={"use_embeddings": True, "embedding_backend": "hash", "policy_llm_chunking": False},
    )
    assert len(store.rules) >= 5, store.last_ingest_stats
    assert store.all_text()
    hits = store.retrieve_rules("return within 30 days full refund", k=3, category="returns")
    assert hits, hits
    # Incremental reuse: rebuild should reuse all section hashes.
    stats1 = dict(store.last_ingest_stats)
    store2 = PolicyStore(
        "data/policy.pdf",
        config={"use_embeddings": True, "embedding_backend": "hash", "policy_llm_chunking": False},
    )
    assert store2.last_ingest_stats["reprocessed"] == 0, store2.last_ingest_stats
    assert store2.last_ingest_stats["reused"] == stats1["rules"]
    print("policy_store self-check OK", store.last_ingest_stats)


if __name__ == "__main__":
    _demo()
