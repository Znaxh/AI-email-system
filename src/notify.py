"""Notifications for items needing a human. Two surfaces:
  - in-app: the Review dashboard reads queue_store.counts() for its badge (no code here).
  - email digest: summarize pending review + escalation items and send via the
    connected email source.

# ponytail: digest fires on sync / on demand; a scheduled cron is future work.
"""

from __future__ import annotations

from src import email_source, queue_store
from src.config import DEFAULTS
from src.schema import IncomingEmail


def compose_digest() -> str:
    esc = queue_store.list_items(decision="escalate", status="pending")
    rev = queue_store.list_items(decision="review", status="pending")
    if not esc and not rev:
        return ""
    lines = [f"Support queue digest — {len(esc)} escalation(s), {len(rev)} awaiting review.", ""]
    for label, items in (("ESCALATIONS (act first)", esc), ("NEEDS REVIEW", rev)):
        if not items:
            continue
        lines.append(f"## {label}")
        for it in items:
            reason = it["judge"].get("escalate_reason") or it["judge"].get("policy_requires") or ""
            lines.append(
                f"- [{it['category']}] {it['subject'] or '(no subject)'} "
                f"· order {it['order_id'] or 'n/a'} · confidence {it['confidence']}"
                + (f" · {reason}" if reason else "")
            )
        lines.append("")
    return "\n".join(lines).strip()


def send_digest(config: dict | None = None) -> dict:
    cfg = {**DEFAULTS, **(config or {})}
    recipient = (cfg.get("digest_recipient") or "").strip()
    if not recipient:
        return {"ok": False, "detail": "no digest recipient configured"}
    body = compose_digest()
    if not body:
        return {"ok": False, "detail": "nothing pending — no digest sent"}
    to = IncomingEmail(id="digest", from_addr=recipient, subject="Support queue digest")
    # a digest always goes out for real when requested (not gated by live_send)
    result = email_source.send_reply(to, body, dry_run=False, config=cfg)
    return {"ok": result.get("ok", False), "detail": result.get("detail", ""), "preview": body}
