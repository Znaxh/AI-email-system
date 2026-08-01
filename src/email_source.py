"""Pluggable inbox connector — one interface, mirroring src/llm_client.

Providers (config["email_source"]):
  - "demo": offline mode. No sample mailbox ships with the app; fetch returns
            an empty list. Sends are recorded locally and never leave the box.
            Optional: drop a JSON array of IncomingEmail-shaped objects at
            data/demo_inbox.json if you want a local fixture for development.
  - "mcp" : the app is an MCP client to a Gmail (or any) MCP server. Connects to
            MCP_SERVER_URL with bearer MCP_AUTH_TOKEN via the mcp Python SDK,
            discovers tools, and maps them by capability to search / get / send.
            Tool names are overridable in config for non-standard servers.

send_reply honors dry_run: in dry-run nothing is actually sent (the default,
gated by config["live_send"] upstream) — it just reports what it would do.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from src.config import DEFAULTS
from src.schema import IncomingEmail

DEMO_INBOX = Path("data") / "demo_inbox.json"

# capability -> substrings that identify a matching MCP tool name (first hit wins)
_TOOL_HINTS = {
    "search": ["search_threads", "search_messages", "list_messages", "search", "list"],
    "get": ["get_message", "get_thread", "read_message", "get", "read"],
    "send": ["send_message", "send_email", "send", "create_draft", "reply"],
}


# --------------------------------------------------------------------- demo ---
def _demo_fetch(limit: int) -> list[IncomingEmail]:
    if not DEMO_INBOX.exists():
        return []
    raw = json.loads(DEMO_INBOX.read_text())
    return [IncomingEmail(**e) for e in raw[: limit or None]]


def _demo_send(email: IncomingEmail, body: str, dry_run: bool) -> dict:
    action = "would send (dry-run)" if dry_run else "sent (demo — not delivered)"
    return {"ok": True, "dry_run": dry_run, "detail": f"{action} to {email.from_addr or 'customer'}"}


# ---------------------------------------------------------------------- mcp ---
def _pick_tool(tool_names: list[str], capability: str, override: str = "") -> str | None:
    """Resolve an MCP tool name for a capability. Pure — unit-tested offline."""
    if override:
        return override if override in tool_names else None
    for hint in _TOOL_HINTS[capability]:
        for name in tool_names:
            if hint in name.lower():
                return name
    return None


def _text_of(result) -> str:
    """Flatten an MCP tool result's content blocks to text."""
    parts = []
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _parse_emails(payload: str, limit: int) -> list[IncomingEmail]:
    """Best-effort map of a server's message JSON onto IncomingEmail.
    Field names vary by server; adjust here if a server uses other keys."""
    try:
        data = json.loads(payload)
    except (json.JSONDecodeError, TypeError):
        return []
    if isinstance(data, dict):
        data = data.get("messages") or data.get("threads") or data.get("results") or [data]
    emails = []
    for m in data[: limit or None]:
        if not isinstance(m, dict):
            continue
        emails.append(
            IncomingEmail(
                id=str(m.get("id") or m.get("message_id") or m.get("threadId") or ""),
                thread_id=str(m.get("thread_id") or m.get("threadId") or ""),
                from_addr=str(m.get("from") or m.get("from_addr") or m.get("sender") or ""),
                subject=str(m.get("subject") or ""),
                body=str(m.get("body") or m.get("snippet") or m.get("text") or ""),
                received_at=str(m.get("date") or m.get("received_at") or ""),
            )
        )
    return [e for e in emails if e.id]


async def _mcp_call(tool_capability_args: list[tuple], cfg: dict):
    """Open one MCP session and run a sequence of (capability, args) calls."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    url = os.environ["MCP_SERVER_URL"]
    token = os.getenv("MCP_AUTH_TOKEN", "")
    headers = {"Authorization": f"Bearer {token}"} if token else None

    async with streamablehttp_client(url, headers=headers) as (read, write, *_):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = [t.name for t in (await session.list_tools()).tools]
            overrides = {
                "search": cfg.get("mcp_tool_search", ""),
                "get": cfg.get("mcp_tool_get", ""),
                "send": cfg.get("mcp_tool_send", ""),
            }
            results = []
            for capability, args in tool_capability_args:
                name = _pick_tool(tools, capability, overrides[capability])
                if not name:
                    raise RuntimeError(f"no MCP tool found for '{capability}' (server tools: {tools})")
                results.append(await session.call_tool(name, args))
            return results


def _mcp_fetch(limit: int, cfg: dict) -> list[IncomingEmail]:
    import asyncio

    query = cfg.get("mcp_search_query", "is:unread")
    (res,) = asyncio.run(_mcp_call([("search", {"query": query, "max_results": limit})], cfg))
    return _parse_emails(_text_of(res), limit)


def _mcp_send(email: IncomingEmail, body: str, dry_run: bool, cfg: dict) -> dict:
    if dry_run:
        return {"ok": True, "dry_run": True, "detail": f"would send to {email.from_addr or 'customer'}"}
    import asyncio

    args = {"to": email.from_addr, "thread_id": email.thread_id, "body": body, "subject": f"Re: {email.subject}"}
    (res,) = asyncio.run(_mcp_call([("send", args)], cfg))
    return {"ok": True, "dry_run": False, "detail": _text_of(res)[:200] or "sent"}


# ------------------------------------------------------------------ dispatch ---
def fetch_unread(limit: int = 20, config: dict | None = None) -> list[IncomingEmail]:
    cfg = {**DEFAULTS, **(config or {})}
    if cfg["email_source"] == "mcp":
        return _mcp_fetch(limit, cfg)
    return _demo_fetch(limit)


def send_reply(email: IncomingEmail, body: str, *, dry_run: bool, config: dict | None = None) -> dict:
    cfg = {**DEFAULTS, **(config or {})}
    if cfg["email_source"] == "mcp":
        return _mcp_send(email, body, dry_run, cfg)
    return _demo_send(email, body, dry_run)


def _demo_test() -> None:
    """Offline self-check: tool-name resolution + parsing (no network)."""
    names = ["search_threads", "get_message", "create_draft", "list_labels"]
    assert _pick_tool(names, "search") == "search_threads"
    assert _pick_tool(names, "get") == "get_message"
    assert _pick_tool(names, "send") == "create_draft"
    assert _pick_tool(names, "send", override="list_labels") == "list_labels"
    assert _pick_tool(names, "send", override="nope") is None

    parsed = _parse_emails(json.dumps({"messages": [
        {"id": "m1", "from": "a@x.com", "subject": "hi", "snippet": "hello there"},
    ]}), limit=10)
    assert parsed and parsed[0].id == "m1" and parsed[0].body == "hello there"
    print("email_source self-check OK")


if __name__ == "__main__":
    _demo_test()
