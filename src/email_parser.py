"""Normalize a raw IncomingEmail into a clean, consistent shape before anything
downstream (classifier, retrieval, generator) touches it, plus a best-effort
inbound-authenticity signal.

Two responsibilities, run together as one pipeline stage:
  1. Parse & normalize the body — strip HTML, drop quoted history/signatures,
     collapse whitespace, fold smart punctuation — so every downstream stage
     sees the same shape of text regardless of which connector (demo | mcp |
     a future IMAP source) produced it.
  2. Auth signal — read SPF/DKIM/DMARC off the provider's own
     Authentication-Results header when the connector supplies one (Gmail-style
     APIs stamp this). There's no raw SMTP access at this layer, so this only
     surfaces what the provider already verified; it never fabricates a pass
     when the signal is absent (the demo inbox has no headers, so it correctly
     reports "unavailable" rather than a fake pass).
"""

from __future__ import annotations

import html
import re

from src.schema import IncomingEmail

_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RUN_RE = re.compile(r"\n{3,}")
_WS_RE = re.compile(r"[ \t]+")

# Quoted-history / signature markers — body is truncated at the first match so
# retrieval and the LLM prompt see only the new message, not the whole thread.
_QUOTE_MARKERS = [
    re.compile(r"\r?\nOn .{0,80} wrote:\s*$", re.MULTILINE),
    re.compile(r"\r?\n-{2,}\s*Original Message\s*-{2,}", re.IGNORECASE),
    re.compile(r"\r?\nFrom:\s*.+\r?\nSent:\s*.+", re.IGNORECASE),
    re.compile(r"\r?\n>.*(\r?\n>.*)*$"),  # trailing '>' quote block
    re.compile(r"\r?\n--\s*\r?\n"),  # conventional signature delimiter
]

_SMART_PUNCT = str.maketrans({
    "‘": "'", "’": "'", "“": '"', "”": '"',
    "–": "-", "—": "-", " ": " ", "​": "",
})


def _strip_html(text: str) -> tuple[str, bool]:
    if "<" not in text or ">" not in text:
        return text, False
    stripped = html.unescape(_TAG_RE.sub(" ", text))
    return stripped, stripped != text


def _strip_quoted(text: str) -> tuple[str, bool]:
    for pattern in _QUOTE_MARKERS:
        m = pattern.search(text)
        if m:
            return text[: m.start()], True
    return text, False


def _normalize_whitespace(text: str) -> str:
    text = text.translate(_SMART_PUNCT)
    text = _WS_RE.sub(" ", text)
    text = _BLANK_RUN_RE.sub("\n\n", text)
    return text.strip()


def _auth_from_headers(headers: dict) -> tuple[str, str]:
    """Best-effort read of a provider-supplied Authentication-Results header.
    Never runs SPF/DKIM/DMARC itself — only surfaces what the provider checked."""
    raw = headers.get("authentication-results") or headers.get("Authentication-Results", "")
    if not raw:
        return "unavailable", "no Authentication-Results header from connector"
    low = raw.lower()
    results = {p: "pass" in low.split(f"{p}=")[1][:10] for p in ("spf", "dkim", "dmarc") if f"{p}=" in low}
    if not results:
        return "unavailable", raw[:200]
    if all(results.values()):
        return "pass", raw[:200]
    failed = [p for p, ok in results.items() if not ok]
    return "fail", f"failed: {', '.join(failed)} — {raw[:150]}"


def parse(email: IncomingEmail) -> IncomingEmail:
    """Return a new IncomingEmail with a cleaned body and auth signal attached.
    Never raises on malformed input — worst case the body ends up empty and
    parse_flags records why, so the classifier can still triage empty bodies to
    ignore instead of the pipeline crashing on it."""
    flags: list[str] = []
    body = email.body or ""

    body, changed = _strip_html(body)
    if changed:
        flags.append("html_stripped")

    body, changed = _strip_quoted(body)
    if changed:
        flags.append("quote_stripped")

    cleaned = _normalize_whitespace(body)
    if not cleaned:
        flags.append("empty_after_clean")

    auth_status, auth_detail = _auth_from_headers(email.raw_headers)

    return email.model_copy(update={
        "body": cleaned,
        "auth_status": auth_status,
        "auth_detail": auth_detail,
        "parse_flags": flags,
    })


def _demo() -> None:
    """Offline self-check: HTML stripping, quote/signature truncation,
    whitespace normalization, and the auth-header reader — no network, no LLM."""
    p = parse(IncomingEmail(id="e1", body="<p>Hello <b>there</b></p><br/>Order #123"))
    assert p.body == "Hello there Order #123", p.body
    assert "html_stripped" in p.parse_flags

    p2 = parse(IncomingEmail(
        id="e2",
        body="Please refund my order.\n\nOn Mon, Jan 1, 2026 at 3:00 PM, Support <s@x.com> wrote:\n> old reply",
    ))
    assert p2.body == "Please refund my order.", repr(p2.body)
    assert "quote_stripped" in p2.parse_flags

    p3 = parse(IncomingEmail(id="e3", body="Thanks for the help!\n--\nJane Doe\nSent from my iPhone"))
    assert p3.body == "Thanks for the help!", repr(p3.body)

    p4 = parse(IncomingEmail(id="e4", body="Hi’s   there\n\n\n\nmulti   space"))
    assert p4.body == "Hi's there\n\nmulti space", repr(p4.body)

    p5 = parse(IncomingEmail(id="e5", body="   "))
    assert p5.body == ""
    assert "empty_after_clean" in p5.parse_flags

    assert parse(IncomingEmail(id="e6", body="hi")).auth_status == "unavailable"

    passed = parse(IncomingEmail(id="e7", body="hi", raw_headers={
        "authentication-results": "mx.google.com; spf=pass smtp.mailfrom=x.com; dkim=pass; dmarc=pass"
    }))
    assert passed.auth_status == "pass"

    failed = parse(IncomingEmail(id="e8", body="hi", raw_headers={
        "authentication-results": "mx.google.com; spf=fail smtp.mailfrom=x.com; dkim=pass; dmarc=fail"
    }))
    assert failed.auth_status == "fail"
    assert "spf" in failed.auth_detail and "dmarc" in failed.auth_detail

    print("email_parser self-check OK")


if __name__ == "__main__":
    _demo()
