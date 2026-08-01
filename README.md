# AI Email Suggested-Response System

A complete, runnable system that suggests customer-support email replies grounded in a
company's **own data** — its policy document, its transaction records, and the replies its
agents actually sent — and measures accuracy with **two unblended scores**: automated RAGAS
response quality (Tier 1) and human-feedback system reliability (Tier 2).

## ▶ How to run & access the app

The UI is a **React + Vite** app (`web/`) served by a thin **FastAPI** backend
(`api.py`) that wraps the pipeline in `src/`.

```bash
# one-time setup (skip if .venv already exists)
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then add your API key(s) — or do it later in the app's Settings page

# --- single process (build once, serve build + API together) ---
cd web && npm install && npm run build && cd ..
uvicorn api:app --port 8000                 # open http://localhost:8000

# --- development (two processes, hot reload) ---
uvicorn api:app --reload --port 8000        # backend
cd web && npm run dev                        # frontend → http://localhost:5173 (proxies /api)
```

You land on the **✉️ Assistant** page; the left nav switches between the five pages:

| Page | What you do there |
|---|---|
| **✉️ Assistant** (landing) | Paste a customer email → click **Suggest a reply**. Expand **"How accurate is this reply?"** for on-demand RAGAS scoring (faithfulness / relevancy / context precision). |
| **📥 Inbox** | **Sync inbox** to fetch unread mail from the connected source (MCP email server, or offline demo mode) and route every message through the pipeline; or paste a single email to route it. Each email is classified **auto-reply / needs-review / escalate / ignore** and dropped into the queue. |
| **🗂️ Review** | The human-action dashboard: Escalations · Needs review · Auto · Audit sample · Done. Send/edit/dismiss, flag hallucinations, and confirm escalation audits — every action writes a `feedback_events` row. |
| **⚙️ Settings** | **Company data** (policy + transactions + optional tickets: preview, map, dry-run, activate); example replies; LLM providers; email connector; automation thresholds; **evaluation gates**; **storage** (local / Postgres / S3 / Azure / GCS). |
| **📊 Evaluation** | Two unblended panels: Response Quality (RAGAS) and System Reliability (human feedback with n + Wilson CIs). |

No API key yet? The app still opens — configure a provider on the Settings page first.
Before Assistant / Inbox can generate, activate **company data** (policy + transactions)
in Settings. Sample fixtures live under `tests/fixtures/northpeak/` for local trials.

```
incoming email
      │
      ├─► PolicyStore — format-aware ingest + hybrid retrieve (BM25 + embeddings + RRF)
      │                 cache via BlobStore (policy_index/{hash}/*)
      ├─► intent scope — categories from the loaded policy
      ├─► TicketRetriever — TF-IDF (ticket corpus + user_examples)
      └─► transaction lookup — active company bundle
      ▼
   generator ──► reply + cited_rules + structured remedy
      ▼
   RAGAS evaluator ──► faithfulness · answer relevancy · context precision
                       + dual-pass retrieval disagreement (forced before AUTO)
                       + deterministic + per-email data-quality flags
                       ──► ragas_scores (StructuredStore)
      ▼
   router ──► AUTO / REVIEW / ESCALATE / IGNORE
              hard gate: faithfulness < gate OR disagreement OR scoring error OR flags → never AUTO
      ▼
   queue (StructuredStore) ──► Review dashboard
                                  │  Send / edit / dismiss / flag hallucination / audit
                                  ▼
                             feedback_events ──► reliability
                                                 critical_error_rate · reliability_rate
                                                 calibration (every rate: n + Wilson CI)

Storage (pluggable): StructuredStore + BlobStore — default local; opt-in Postgres / S3 / Azure / GCS
Company data: uploaded via Settings → versioned under company/ in BlobStore
```

## 1. Quick start (batch pipeline)

Setup is the same as "How to run & access the app" above. Activate company data
first (Settings → Company data, or upload the sample fixtures — see §3), then:

```bash
python pipeline.py --all      # generate → RAGAS evaluate → reliability report
python pipeline.py --all --limit 1   # frugal trial on one holdout ticket
```

`LLM_PROVIDER=anthropic|openai|mistral` selects the provider for **email generation**
(and RAGAS judges). `CLASSIFY_LLM_PROVIDER` / `CLASSIFY_LLM_MODEL` select the model
used only for **email categorization** (falls back to `LLM_*` when unset). Both go through
one interface in `src/llm_client.py`. RAGAS uses native `llm_factory` for all three providers
and `embedding_factory("huggingface", ...)` for local sentence-transformers embeddings
(hash-embedding shim only for offline mock).

*(A third provider value, `mock`, exists purely to smoke-test the plumbing offline with a
deterministic stub — its scores are meaningless and it is not part of the submission flow.)*

Storage defaults to local SQLite + filesystem (`src/storage/`). Opt into Postgres / S3 /
Azure / GCS from Settings; secrets stay in `.env`.

## 2. The core design principle: company-agnostic code

**Everything company-specific is uploaded through Settings and stored as a versioned
company-data bundle. Nothing in `src/` names a company, a product, or a rule.**

Activate a policy document (PDF/DOCX/MD/txt), a transactions export (CSV/TSV/XLSX/JSON),
and optionally past tickets. Column mapping is canonical-keyed; dry-run returns
`READY` / `DEGRADED` / `BLOCKED` before anything is activated. Failed uploads leave
the prior active version serving.

- `src/company_data/` — preview, map, dry-run, normalize, activate, rollback.
- `src/policy_store.py` ingests **any** supported policy, extracts structured rules
  (including `depends_on` for per-email quality gates), and caches under
  `policy_index/{hash}/*`.
- `src/retriever.py` fits TF-IDF over the activated ticket corpus (plus optional
  production `user_examples` in the live UI). Thin corpora disable neighbor retrieval
  rather than inventing near-random tone matches.
- `src/schema.py` uses a generic transaction shape (`extra="allow"`).
- Every prompt in `src/prompts.py` injects policy text, transaction data and past replies at
  runtime; none contains a company fact. Past tickets teach **voice/tone only** and must never
  override policy.

That is the point of the design: this is a *product*, not a demo hard-coded to one dataset.

## 3. Sample fixtures (optional)

Demo company files are **not** shipped under `data/` anymore. The only tracked sample
set is:

```
tests/fixtures/northpeak/
  policy.pdf
  transactions.json
  dataset.json          # optional past tickets (corpus + holdout)
```

**How to use them**

1. **Preferred:** Settings → Company data → upload `policy.pdf` + `transactions.json`
   (and optionally `dataset.json` as tickets) → map columns → dry-run → activate.
2. **One-time legacy bootstrap:** copy those three files into a local `data/` folder
   (`policy.*` + `transactions.json` required; `dataset.json` optional). On first boot
   with no `company/active.json`, the app imports them once. `data/*` company files are
   gitignored — they never re-enter the repo.

Production deployments should upload their own policy and order export. To regenerate
the sample policy PDF from markdown, use the dev-only helper
`scripts/dev/build_policy_pdf.py` (not part of the runtime path).

**Holdout is never retrievable.** Ticket `split` defaults to `corpus`. A ticket becomes
holdout only when a split column is explicitly mapped *and* carries a recognized holdout
label. Unrecognized values fall back to `corpus`.

**No hand-labeled answer key and no synthetic control replies.** Accuracy trust comes from
organic Review-dashboard feedback (`feedback_events`) plus reference-free RAGAS metrics.

## 4. Generation approach — RAG over policy + past tickets, and why

For each incoming email the generator (`src/generator.py`) retrieves the top policy clauses
and the top similar past tickets, and prompts the LLM with both plus the transaction record.
The prompt is explicit about the hierarchy: **policy determines the remedy; past tickets
teach only voice and structure; escalation rules are checked first.** The model also emits a
structured remedy object (`remedy_type`, `remedy_amount`, `rule_cited`, `escalate`) used later
for deterministic edit classification — not for LLM-judged scoring.

Why this combination beats the alternatives:

- **Policy alone** tells you the rule but not the house voice, reply structure, or precedent
  for handling emotion — replies come out legally correct but robotic.
- **Past tickets alone** can't guarantee the historical agent followed policy correctly, and
  can't answer scenarios with no precedent (our holdout deliberately contains one).
  Retrieval also drifts: a similar-sounding email can have the opposite correct outcome
  (in-window vs final-sale return look nearly identical textually).
- **Combining both** is robust to each one's failure mode — and RAGAS faithfulness closes the
  loop by checking claims against the *retrieved* policy chunks.
- **vs fine-tuning:** fine-tuning bakes today's policy into weights. Real policies version
  (ours is stamped v3.1); real ticket corpora grow daily. With RAG, updating the system is
  *replacing a file*. Fine-tuning also needs orders of magnitude more data than any single
  team's corpus, costs money per iteration, and can't cite the rule it applied.
- **vs zero-shot:** ignores the owned data entirely — no grounding in the actual policy, no
  house voice, and (as the task requires) no use of the dataset at all.
- **Retrieval choice:** hybrid BM25 + local `sentence-transformers` embeddings, fused with
  Reciprocal Rank Fusion, after intent-based category scoping (plus always-on `global`
  escalation rules). Optional cross-encoder rerank is off by default. Section content-hashes
  make re-uploads reprocess only changed sections (`results/policy_index/` via BlobStore).
  Ticket retrieval remains TF-IDF over the corpus (and Settings-managed `user_examples`).

## 5. Accuracy — two numbers, never blended

### Tier 1 — RAGAS Response Quality (automated, zero setup)

| Metric | What it catches |
|---|---|
| **Faithfulness** | Unsupported claims vs retrieved policy chunks (hallucinations) |
| **Answer relevancy** | On-topic-sounding but non-responsive drafts |
| **Context precision** | Retrieved chunks that were not actually useful (retriever quality) |

Scores are stored individually in the `ragas_scores` table. For routing only:

```
quality_score = 0.5·faithfulness + 0.3·answer_relevancy + 0.2·context_precision   # 0–1
```

**Hard AUTO gate:** `faithfulness < FAITHFULNESS_GATE` (default 0.7) **or**
`retrieval_disagreement == true` **or** scoring failure → never AUTO.
Disagreement is a sampled dual-pass check (top-k cited rule vs full-document rule extraction)
— no hand labels required. Deterministic length/placeholder/absolute-claim checks remain as
non-blended diagnostic flags.

### Tier 2 — System Reliability (human feedback only)

Review actions produce mutually exclusive labels: `ACCEPTED_AS_IS`, `EDITED_MINOR`,
`EDITED_MAJOR`, `REJECTED`, `ESCALATED_CORRECTLY`, `ESCALATED_MISSED`,
`FLAGGED_HALLUCINATION`. Minor vs major edits are classified by structured remedy diff
(not another LLM judgment).

**Critical error rate** (business headline) uses only human-labeled AUTO responses as the
denominator — unaudited AUTO is excluded and audit coverage is reported separately:

```
critical_error_rate =
  count(label ∈ {EDITED_MAJOR, REJECTED, ESCALATED_MISSED, FLAGGED_HALLUCINATION}
        ∧ routing_decision == AUTO ∧ labeled)
  / count(routing_decision == AUTO ∧ labeled)
```

Every rate carries `n` and a Wilson 95% CI; slices with `n < 20` render as insufficient data.
Calibration buckets RAGAS `quality_score` deciles against real acceptance — if a high-score
bucket has high critical-error rate, tighten the faithfulness gate.

## 6. Validating trustworthiness

`src/validate_metric.py` / the Evaluation dashboard report reliability from organic
`feedback_events` — not from synthetic controls or hand-labeled answer keys. A brand-new
customer with only a policy PDF gets full Tier-1 scoring on response one. The number an
owner sees first (`critical_error_rate`) is computed exclusively from actions humans took.

## 6b. The automation layer — inbox → route → review

The suggested-reply engine and the validated metric are the hard part; the automation layer wraps
them into an end-to-end support system. **The metric is the control system**: it decides how much
autonomy each reply earns.

```
inbox (MCP email server | offline demo)  ── src/email_source.py ──► IncomingEmail[]
        │
        ▼
   src/email_parser.py   strip HTML/quoted-history/signatures, normalize whitespace,
        │                surface SPF/DKIM/DMARC when the connector supplies headers
        ▼
   src/event_bus.py      StructuredStore table "event_bus" — publish → drain():
        │                each email isolated; retry ×3, else dead-letter
        ▼
   src/classifier.py   LLM categorizes into refund / cancellation / complain /
        │               billing / technical_support / general_inquiry / other (noise)
        ▼
   src/router.py   generate_reply → RAGAS evaluate → decide
        │   AUTO      quality_score high + faithfulness gate pass + no disagreement
        │   REVIEW    mid confidence, gated from AUTO, or deterministic flags
        │   ESCALATE  remedy.escalate from generator, or confidence < T2
        │   IGNORE    category is other (noise) — no generation/eval spent
        ▼
   src/queue_store.py (StructuredStore "queue") ──► 🗂️ Review dashboard
        │                                            feedback_events on every action
        └── src/notify.py  ── email digest of pending items via the same connector
```

- **Live confidence** = `quality_score × 100 − deterministic penalty` (0–100). AUTO is blocked when
  faithfulness is below the gate, retrieval disagreement is true, scoring failed, or
  deterministic flags fire.
- **Escalation stays company-agnostic.** The generator's structured `remedy.escalate` is derived
  from the policy text — the router never hardcodes a rule id. Swap the policy document and
  escalation behavior changes with it.
- **Email connector.** One pluggable interface (mirroring `llm_client`). `demo` is offline
  (empty inbox by default; dry-run sends only); `mcp` makes the app an **MCP client** to a
  Gmail (or any) MCP server — server URL + token in Settings, tools auto-mapped by capability.
- **Dry-run by default.** Nothing is actually sent until the **live-send** switch is on. Thresholds,
  evaluation gates, storage backends, and notifications are all changed from Settings.

## 7. Repo map

```
tests/fixtures/northpeak/        tracked sample policy + txn + tickets (upload via Settings)
scripts/dev/build_policy_pdf.py  optional helper to rebuild the sample policy PDF
data/README.md                   explains local-only legacy bootstrap (company files gitignored)
src/company_data/                upload preview · dry-run · normalize · activate · rollback
src/policy_ingest.py             PDF/DOCX/Markdown/txt → sections + depends_on
src/policy_store.py              hybrid BM25 + embeddings + RRF (versioned BlobStore index)
src/intent.py                    retrieval intent scoping from loaded policy categories
src/retriever.py                 TF-IDF over past-ticket corpus (+ user_examples in live UI)
src/generator.py                 policy + precedent → reply + cited_rules + remedy
src/ragas_evaluator.py           Tier-1 faithfulness / relevancy / context precision + disagreement
src/evaluator.py                 RAGAS orchestration + deterministic diagnostics
src/reliability.py               Tier-2 rates (Wilson CI) + calibration
src/feedback.py               Review-action labels → feedback_events
src/validate_metric.py        reliability report from feedback_events
src/storage/                  pluggable StructuredStore + BlobStore (local default)
src/email_source.py           pluggable inbox connector (demo offline | mcp)
src/email_parser.py           normalize a fetched email
src/event_bus.py              ingestion queue via StructuredStore
src/classifier.py             LLM categorization (7 categories)
src/router.py                 email → AUTO / REVIEW / ESCALATE / IGNORE
src/queue_store.py            review queue via StructuredStore
src/notify.py                 email digest of pending items
src/config.py                 non-secret runtime config (config.json)
pipeline.py                   batch CLI: --all | --generate | --evaluate | --validate
api.py                        FastAPI JSON wrapper over src/ (serves web/dist + SPA fallback)
src/app_data.py               app-level data helpers (env, user examples) shared by api.py
web/                          React + Vite frontend (Assistant · Inbox · Review · Settings · Evaluation)
company/                      active + versioned company-data blobs (gitignored; BlobStore)
results/                      local runtime only (gitignored; empty on a fresh clone)
```

## 8. Future implementation — from assistant to autonomous support layer

Everything below builds on what already exists: the company-agnostic RAG core, the
**validated accuracy metric**, and Settings-uploaded company-data bundles. The metric is
the key — it stops being a report card and becomes the **control system** that decides how
much autonomy the product is allowed.

### Target architecture

```
Gmail / IMAP / helpdesk API (Hiver, Zendesk, ...)
      │  inbox sync (Gmail API watch + Pub/Sub push, no polling)
      ▼
Ingestion & PII redaction ──► thread reconstruction · attachment/OCR parsing
      ▼
Triage classifier (small/cheap model)
      │  category · sentiment · urgency · language · is-support vs noise
      ▼
Router ──────────────┬──────────────────────┬─────────────────────┐
      ▼              ▼                      ▼                     ▼
 AUTO-REPLY     DRAFT FOR REVIEW      ESCALATE TO HUMAN      IGNORE/ARCHIVE
 (score ≥ T₁)   (T₂ ≤ score < T₁)     (score < T₂, or        (newsletters,
      │              │                 policy says so:        auto-replies)
      │              │                 high-value, legal,
      │              │                 repeat contact)
      ▼              ▼                      ▼
 RAG generator ──► RAGAS gate (faithfulness + disagreement) ──► send / queue
      ▲                                                              │
      └────────── learning loop: agent edits → feedback_events ◄─────┘
```

The generator and evaluator in the middle are **exactly the modules in this repo** —
`src/generator.py`, `src/ragas_evaluator.py`, and `src/evaluator.py` — promoted from batch
tools to online services.

### Roadmap

**Status:** items 1–4 below are now **implemented** in this repo (see §6b) — an MCP email connector
(plus offline demo mode) with a normalization + durable-queue front end, a cheap keyword
triage/noise gate as its own stage, confidence-gated routing with a dry-run auto-send switch, and a
human-in-the-loop review dashboard with an email digest. Items 5–9 remain the forward path.
Details of what shipped:

- **Inbox integration** via the **MCP** connector (`src/email_source.py`) — the "same adapter
  interface covers any provider" idea, realized as one pluggable connector selected in Settings.
  (Gmail-API OAuth / IMAP / Pub/Sub push are the same-interface extensions still to add.)
- **Ingestion normalization + a durable queue** — `src/email_parser.py` strips HTML, quoted-history,
  and signature blocks and normalizes whitespace/punctuation before anything downstream sees the
  email, plus reads a provider's `Authentication-Results` header for SPF/DKIM/DMARC when one is
  supplied. `src/event_bus.py` (SQLite) sits between fetch and routing so a batch sync survives one
  email's failure — that email retries and dead-letters instead of crashing the sync. This is a
  slice of target-architecture item 1 below (full thread reconstruction and PII redaction are not
  built yet).
- **Triage** is an LLM/SLM classifier (`src/classifier.py`) that labels each email
  refund / cancellation / complain / billing / technical_support / general_inquiry / other
  and IGNORE-routes `other` before generation. Provider/model are chosen separately from
  generation in Settings (`CLASSIFY_LLM_*`).
- **Confidence-gated auto-reply** is the T1/T2 thresholding in `src/router.py`, gated further by
  zero flags + a cited rule + no policy-mandated escalation, with a global dry-run/live-send switch.
- **Human-in-the-loop** is the 🗂️ Review dashboard + SQLite queue + email digest.

1. **Gmail inbox integration.** OAuth per mailbox, Gmail API `watch` + Pub/Sub for
   real-time push (no polling), full thread reconstruction so the model sees the
   conversation, not one message, and PII redaction on ingest. The same adapter interface
   covers IMAP and helpdesk APIs (Hiver, Zendesk, Front) so the core never knows which inbox
   it serves. *(Email fetch + body normalization + a durable ingestion queue already exist;
   OAuth/watch/Pub-Sub, full thread reconstruction, and PII redaction do not yet.)*

2. **Triage & categorization.** A small/cheap model labels every incoming email into the
   support categories above (with `other` as the noise gate) so the expensive generation
   model stays off non-support mail. *(LLM categorization is in `src/classifier.py`;
   sentiment/urgency/language as separate signals are still open.)*

3. **Confidence-gated auto-reply for straightforward cases.** Every draft is scored by
   the same 3-layer evaluator **before** anything is sent. Score ≥ T₁ *and* zero
   deterministic flags *and* the cited rule is unambiguous → send automatically.
   The thresholds are not guesses: they are calibrated on the validation set exactly the
   way §6 calibrates the metric, and tightened per category until the measured
   false-approve rate is below an agreed SLA. Autonomy is earned by the metric, per
   category, not switched on globally.

4. **Human-in-the-loop for everything else.** Mid-confidence drafts land in the agent's
   inbox as editable suggestions with the grounding attached (policy clauses, transaction,
   precedent tickets — the same transparency panel the Assistant page already shows).
   Low-confidence or policy-mandated cases (high-value orders, legal threats, frequent
   returners — rules the policy already encodes) skip drafting and escalate with a
   one-paragraph brief of what the policy requires. Every accept / edit / reject is
   captured as labeled data.

5. **Beyond complaints: an operational-knowledge policy.** Info requests ("what's your
   sizing?", "do you ship to X?", "where is my invoice?") don't need a remedies policy —
   they need an *operational information document*: shipping matrices, store hours, product
   FAQs, account procedures. Because the whole pipeline is document-agnostic, this is
   literally a second uploaded policy document in the company-data bundle and a routing
   rule: the compliance judge's question changes from "did the reply offer the required
   remedy?" to "is every stated fact present in the operational document?" — same
   structure, same validation method.

6. **Multi-document / versioned policy store.** Hybrid BM25 + embeddings + RRF (with
   section-hash incremental reindexing) is already in place. Next: a **versioned
   multi-document** store with effective dates — so "which policy was in force when this
   order shipped?" has a correct answer, and a policy update triggers automatic
   re-evaluation of the golden set (catching rules the new document silently changed).

7. **Continuous learning & drift detection.** Agent edits become preference pairs for
   the generator; accept/reject decisions continuously re-validate the judge
   (human-vs-judge agreement is monitored the same way §6's check 3 does it once).
   If agreement drifts below threshold, autonomy automatically steps down a tier —
   the system degrades to draft-mode instead of failing silently.

8. **From suggested text to suggested action.** The judge already extracts the required
   remedy in structured form ("full refund of $88.00 under R1.1"). Connect that to
   order-management / payment APIs (Shopify, Stripe) so approving a reply also executes
   the refund — with the same tiered autonomy: auto-execute small refunds, require a
   click for large ones, dual-approval above a limit. This closes the loop on the actual
   business problem: not writing emails, but resolving tickets.

9. **Enterprise hardening.** Multi-tenant data isolation (per-company namespace — the
   company-agnostic design was built for this), PII redaction before any LLM call,
   BYO-model/VPC deployment via the pluggable `llm_client`, immutable audit log of every
   suggestion + its grounding + who approved it, RBAC, per-language support, and
   cost tiering (small model for triage, large for generation, cached embeddings).

**Why this is credible rather than aspirational:** every stage of the diagram is gated by
the metric this submission validates. The hard part of autonomous support isn't generating
text — it's *knowing when the text is safe to send*. That is exactly what was built here.

## 9. AI tools disclosure

This submission was built with Claude Code (Claude Fable 5) doing the implementation under
the direction of a human-authored design brief: architecture, dataset design, metric design
and validation strategy were specified up front; the agent wrote the code, synthesized the
dataset content, and verified the pipeline end-to-end. LLM calls at runtime use the
Anthropic or OpenAI API via the pluggable client in `src/llm_client.py`.
