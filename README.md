# AI Email Suggested-Response System

**Customer-support email assistant with hybrid RAG, RAGAS quality gates, and human-review routing.**

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-backend-009688?style=flat-square&logo=fastapi)](https://fastapi.tiangolo.com)
[![React](https://img.shields.io/badge/React-Vite%20UI-61DAFB?style=flat-square&logo=react)](https://react.dev)
[![RAGAS](https://img.shields.io/badge/RAGAS-evaluation-7C3AED?style=flat-square)](https://docs.ragas.io)

Turn incoming support emails into **grounded suggested replies** using company policy, transactions, and past tickets. Every response passes deterministic quality gates before AUTO / REVIEW / ESCALATE / IGNORE routing.

> Built as a company-agnostic product: upload your own policy + transaction bundle in Settings. No hardcoded business rules in core code.

---

## Highlights

- **Hybrid retrieval:** BM25 + embeddings with reciprocal rank fusion
- **Grounded generation:** cited rules + structured remedy output
- **RAGAS scoring:** faithfulness, answer relevancy, context precision
- **Human-in-the-loop:** review queue, escalation audits, feedback logging
- **Pluggable storage:** local SQLite/filesystem or Postgres / S3 / Azure / GCS
- **Full UI:** Assistant, Inbox, Review, Settings, Evaluation pages

---

## Architecture

```
incoming email
    │
    ├─ PolicyStore (hybrid retrieve + cached index)
    ├─ TicketRetriever (TF-IDF over past tickets)
    └─ transaction lookup (active company bundle)
    ▼
generator → RAGAS evaluator → router (AUTO / REVIEW / ESCALATE / IGNORE)
    ▼
review queue + feedback_events → reliability reporting
```

See [architecture.md](architecture.md) for the full design write-up.

---

## Tech stack

| Layer | Tools |
|---|---|
| Backend | Python, FastAPI, Pydantic |
| Frontend | React, Vite |
| Retrieval | BM25, sentence-transformers, hybrid RRF |
| Evaluation | RAGAS |
| LLM providers | Anthropic, OpenAI, Mistral (configurable) |
| Storage | SQLite default; optional Postgres / object stores |

---

## Quick start

```bash
git clone https://github.com/Znaxh/AI-email-system.git
cd AI-email-system

python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env

# Build frontend + run API
cd web && npm install && npm run build && cd ..
uvicorn api:app --port 8000
```

Open http://localhost:8000

**Dev mode (hot reload):**

```bash
uvicorn api:app --reload --port 8000   # terminal 1
cd web && npm run dev                     # terminal 2 → http://localhost:5173
```

### First-time setup in the UI

1. Open **Settings** → add an LLM provider API key
2. Upload **company data** (policy + transactions; sample fixtures in `tests/fixtures/`)
3. Run dry-run activation
4. Use **Assistant** or **Inbox** to route emails

---

## UI pages

| Page | Purpose |
|---|---|
| **Assistant** | Paste an email → get a suggested reply + on-demand RAGAS scores |
| **Inbox** | Sync/route messages into the review queue |
| **Review** | Human send/edit/dismiss/escalate actions |
| **Settings** | Company data, providers, thresholds, storage |
| **Evaluation** | Response quality (RAGAS) + human-feedback reliability |

---

## Batch pipeline (CLI)

```bash
python pipeline.py --all
python pipeline.py --all --limit 1   # smoke test on one ticket
```

---

## Project structure

```
AI-email-system/
├── api.py              # FastAPI app + static UI
├── pipeline.py         # Batch generate/eval/report CLI
├── src/                # Core RAG, routing, storage, evaluation
├── web/                # React + Vite frontend
├── tests/
└── architecture.md     # Deep architecture notes
```

---

## Design principle

Everything company-specific is uploaded through Settings and versioned as a bundle. Core `src/` stays company-agnostic so the same engine can serve different support policies.

---

## Author

**Anurag Pratap Singh** · [GitHub](https://github.com/Znaxh)

## License

See repository license terms.
