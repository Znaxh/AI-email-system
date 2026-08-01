# Architecture — AI Email Suggested-Response System

Repo-accurate map of the implemented pipeline. For the interactive Mermaid diagrams and
step cards, open **[`architecture.html`](./architecture.html)** in a browser.

Two unblended scores drive trust:

1. **Tier 1 — RAGAS response quality** (automated, every reply)
2. **Tier 2 — System reliability** (human Review feedback only)

Company-specific facts are uploaded through Settings and stored as a versioned
company-data bundle (BlobStore). Code in `src/` is company-agnostic.

---

## Three entry paths

The UI is a React + Vite app (`web/`) over a thin FastAPI wrapper (`api.py`) around `src/`.

| Path | Entry | What runs |
|---|---|---|
| **Live** | UI Inbox → Review (`/api/inbox/*`, `/api/queue/*`) | fetch → parse → event bus → classify → generate → RAGAS → route → queue → feedback |
| **Batch** | `python pipeline.py --all` | holdout generate → RAGAS evaluate → reliability report (no classifier/router/queue) |
| **Assist** | UI Assistant (`/api/assistant/*`) | `generate_reply` only; optional on-demand RAGAS |

---

## Company-data ingestion (setup)

```
upload (policy / transactions / tickets)
        │
        ▼
preview → map columns → dry_run (READY | DEGRADED | BLOCKED)
        │
        ▼  (only READY, or confirmed DEGRADED)
normalize + versioned BlobStore write
        │
        ▼
build PolicyStore index (policy_index/{hash}/*) + TicketRetriever
        │
        ▼
atomic activate → company/active.json  (failed uploads change nothing)
```

Policy + transactions are required. Ticket history is optional (tone only).
Per-email `unverifiable:*` flags gate AUTO only when a used policy rule depends on
a field marked missing on that transaction — never file-wide.

Tracked sample inputs: `tests/fixtures/northpeak/` (upload via Settings). Demo
files are not shipped under `data/` — that folder is local-only / gitignored for
an optional one-time legacy bootstrap (see `data/README.md`).

---

## Live pipeline (12 stages)

```
email_source.fetch_unread (demo offline | mcp)
        │
        ▼
email_parser.parse          # HTML / quotes / auth_status
        │
        ▼
event_bus.publish → drain   # retry ×3 → dead_letter
        │
        ▼
classifier.classify         # LLM · other → IGNORE (skip generate/eval)
        │
        ▼
detect_order_id + intent    # active transactions · policy category scope
        │
        ├─► PolicyStore     # BM25 + embeddings + RRF (versioned BlobStore cache)
        ├─► TicketRetriever # TF-IDF (corpus + user_examples in live UI)
        └─► transaction (+ per-record _missing_fields)
        ▼
generator.generate_reply    # reply + cited_rules + structured remedy
        │
        ▼
ragas_evaluator             # faithfulness · relevancy · context precision
  + dual-pass disagreement (forced before AUTO)
  + deterministic diagnostics (non-blended)
  + per-email data-quality flags
  → ragas_scores table
        │
        ▼
router._decide              # AUTO / REVIEW / ESCALATE / IGNORE
  hard gate: faithfulness < gate OR disagreement OR scoring error OR flags
  → never AUTO
        │
        ▼
queue_store.upsert          # StructuredStore · optional notify digest
        │
        ▼
Review dashboard            # send / edit / dismiss / flag / audit
  → feedback_events
        │
        ▼
reliability                 # critical_error_rate · n + Wilson CI
```

### Hard AUTO gates

Never AUTO when any of:

- `faithfulness < FAITHFULNESS_GATE` (default `0.7`)
- `retrieval_disagreement == true`
- `scoring_error` non-empty
- disagreement unchecked (fail closed — forced check before AUTO)
- any deterministic / per-email data-quality flag

`retrieval_disagreement` is nullable when sampling skips the check — store
`disagreement_checked` separately; never treat “not checked” as agreement.

### Routing-only quality score (0–1)

```
quality_score = 0.5·faithfulness + 0.3·answer_relevancy + 0.2·context_precision
confidence    = clamp(quality_score × 100 − deterministic_penalty, 0, 100)
```

Tier-1 and Tier-2 numbers are **never blended** in the Evaluation UI.

### Structured remedy

```
{ remedy_type, remedy_amount, rule_cited, escalate }
```

Policy determines the remedy; past tickets teach voice/tone only. At Send time,
remedy field diffs classify `EDITED_MAJOR` vs `EDITED_MINOR` (not another LLM judge).

**AUTO never auto-sends** — it only queues a draft. `live_send` gates Review’s Send.

---

## Batch pipeline

```
active company bundle (normalized policy · txn · tickets)
        │
        ▼
PolicyStore + TicketRetriever(fit corpus only)
        │
        ▼
generate_reply (holdout)  → results/generated_replies.json (+ BlobStore)
        │
        ▼
evaluate_generated (RAGAS) → results/evaluation_results.json + ragas_scores
        │
        ▼
validate_metric (feedback_events → reliability) → results/validation_report.json
```

---

## Storage

| Primitive | Interface | Default | Opt-in |
|---|---|---|---|
| Structured | `get_structured_store()` | Local SQLite | Postgres |
| Blob | `get_blob_store()` | Local filesystem | S3 / Azure / GCS / Postgres |

Tables (local files under `results/`): `queue`, `event_bus`, `feedback_events`,
`ragas_scores`, `user_examples`. Policy index cache: BlobStore `policy_index/{hash}/*`.
Company data: BlobStore `company/versions/*`, `company/active.json`.

---

## Module map

| Module | Role |
|---|---|
| `tests/fixtures/northpeak/` | Tracked sample policy + transactions + tickets (upload via Settings) |
| `data/README.md` | Local-only legacy bootstrap notes (company files gitignored) |
| `scripts/dev/build_policy_pdf.py` | Optional helper to rebuild the sample policy PDF |
| `src/company_data/` | Upload preview · dry-run · normalize · activate · rollback |
| `src/email_source.py` | Inbox connector (`demo` \| `mcp`) |
| `src/email_parser.py` | Normalize body + auth signal |
| `src/event_bus.py` | Ingestion queue, retry ×3, dead-letter |
| `src/classifier.py` | LLM triage; `other` → IGNORE |
| `src/policy_ingest.py` | PDF/DOCX/MD/txt → sections + `depends_on` |
| `src/policy_store.py` | Hybrid BM25 + embeddings + RRF |
| `src/intent.py` | Retrieval scoping from policy categories |
| `src/retriever.py` | TF-IDF past tickets |
| `src/generator.py` | Reply + cited_rules + remedy |
| `src/ragas_evaluator.py` | Tier-1 scores + disagreement + AUTO gate |
| `src/evaluator.py` | RAGAS orchestration + deterministic checks |
| `src/router.py` | AUTO / REVIEW / ESCALATE / IGNORE |
| `src/queue_store.py` | Review queue |
| `src/feedback.py` | Review labels → `feedback_events` |
| `src/reliability.py` | Rates + Wilson CIs + calibration |
| `src/validate_metric.py` | `build_reliability_report()` — reliability entrypoint used by `api.py` |
| `src/storage/` | Pluggable StructuredStore + BlobStore |
| `pipeline.py` | Batch CLI |
| `api.py` | FastAPI JSON wrapper over `src/`; serves `web/dist` + SPA fallback |
| `src/app_data.py` | App-level data helpers (env, user examples) used by `api.py` |
| `web/` | React + Vite frontend (Assistant · Inbox · Review · Settings · Evaluation) |

Open [`architecture.html`](./architecture.html) for the full diagram and stage cards.
