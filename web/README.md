# Web frontend (React) — Nike-bold "broadcast" UI

React + Vite frontend. Five pages (Assistant, Inbox, Review, Settings,
Evaluation), backed by a thin FastAPI layer (`../api.py`) that reuses `src/` — no
company facts live in the frontend or the API. This is the only UI; there is no
Streamlit app.

## Run (development, two processes)

```bash
# 1. backend (repo root)
.venv/bin/uvicorn api:app --reload --port 8000

# 2. frontend (this dir) — proxies /api → :8000
cd web && npm install && npm run dev      # http://localhost:5173
```

## Run (single process, production-style)

```bash
cd web && npm run build      # emits web/dist
.venv/bin/uvicorn api:app --port 8000     # serves dist + API at http://localhost:8000
```

FastAPI serves `web/dist` when it exists (with SPA fallback for deep links); the
Vite dev server is only for hot-reload development.

The Assistant and Inbox pages need an LLM key (set one in `.env` or on the
Settings page) **and** an activated company-data bundle (Settings → Company data:
policy + transactions). Sample files for a local trial live at
`../tests/fixtures/northpeak/` — upload them through Settings (preferred), or copy
them into a local `../data/` folder for a one-time legacy bootstrap (those paths
are gitignored). Review and Evaluation work without an LLM key. The `demo` email
source is an empty offline inbox — point `email_source` at `mcp` (Settings) to
fetch real mail.

## Design

"Broadcast/scoreboard" identity — Anton condensed display type, an electric
ultramarine accent, a live status ticker, and animated scoreboard tallies, over
quiet/legible data surfaces. Tokens live at the top of `src/styles.css`.
