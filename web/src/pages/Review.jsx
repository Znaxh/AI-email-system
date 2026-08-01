import { useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { api } from "../api";
import { useApp } from "../App.jsx";
import { Alert, DecisionChip, Masthead, PageFade, Score, Scoreboard, Spinner } from "../components/ui.jsx";

function Ragas({ ragas, flags }) {
  if (!ragas || Object.keys(ragas).length === 0) return null;
  return (
    <dl className="kv">
      <dt>Faithfulness</dt><dd>{ragas.faithfulness ?? "n/a"}</dd>
      <dt>Answer relevancy</dt><dd>{ragas.answer_relevancy ?? "n/a"}</dd>
      <dt>Context precision</dt><dd>{ragas.context_precision ?? "n/a"}</dd>
      <dt>Quality score</dt><dd>{ragas.quality_score ?? "n/a"}</dd>
      <dt>Disagreement</dt><dd>{String(ragas.retrieval_disagreement)} (checked={String(ragas.disagreement_checked)})</dd>
      <dt>Flags</dt><dd>{flags?.join(", ") || "none"}</dd>
    </dl>
  );
}

function Card({ it, audit, live, onAction }) {
  const [open, setOpen] = useState(audit || (it.decision === "escalate" && it.status === "pending"));
  const [edited, setEdited] = useState(it.suggested_reply || "");
  const [busy, setBusy] = useState("");
  const pending = it.status === "pending";

  async function act(kind, path, body) {
    setBusy(kind);
    try { await onAction(path, body); } finally { setBusy(""); }
  }

  const sendLabel = live ? "Send reply" : "Simulate send";

  return (
    <div className={`qcard ${it.decision}`}>
      <div className="qcard-head" onClick={() => setOpen((o) => !o)}>
        <DecisionChip decision={it.decision} />
        <div className="grow">
          <div className="qcard-title">{it.subject || "(no subject)"}</div>
          <div className="qcard-meta">
            {it.category} · order {it.order_id || "n/a"} · {it.status}
          </div>
        </div>
        <div className="qcard-conf">{it.confidence}<small>conf / 100</small></div>
      </div>
      <AnimatePresence initial={false}>
        {open && (
          <motion.div
            className="qcard-body"
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.28, ease: [0.16, 1, 0.3, 1] }}
            style={{ overflow: "hidden" }}
          >
            <div style={{ paddingTop: 16 }}>
              <div className="field-label">From</div>
              <div className="mono" style={{ marginBottom: 10 }}>{it.from_addr || "n/a"}</div>
              <div className="email-block">{it.body}</div>
              <Ragas ragas={it.ragas} flags={it.flags} />

              {it.decision !== "ignore" && (
                <>
                  <div className="field-label" style={{ marginTop: 14 }}>Suggested reply (edit before sending)</div>
                  <textarea rows={7} value={edited} onChange={(e) => setEdited(e.target.value)} />
                </>
              )}

              {audit && pending ? (
                <div className="btn-row" style={{ marginTop: 14 }}>
                  <button className="btn ghost" onClick={() => act("miss", `/queue/${it.email_id}/audit`, { ok: false })} disabled={!!busy}>
                    {busy === "miss" ? <Spinner dark /> : "Escalation was missed"}
                  </button>
                  <button className="btn primary" onClick={() => act("ok", `/queue/${it.email_id}/audit`, { ok: true })} disabled={!!busy}>
                    {busy === "ok" ? <Spinner /> : "AUTO was fine"}
                  </button>
                </div>
              ) : pending ? (
                <div className="btn-row" style={{ marginTop: 14 }}>
                  {it.decision !== "ignore" && (
                    <button className="btn primary" onClick={() => act("send", `/queue/${it.email_id}/send`, { edited })} disabled={!!busy}>
                      {busy === "send" ? <Spinner /> : sendLabel}
                    </button>
                  )}
                  {it.decision !== "ignore" && (
                    <button className="btn" onClick={() => act("save", `/queue/${it.email_id}/save`, { edited })} disabled={!!busy}>
                      {busy === "save" ? <Spinner dark /> : "Save edit"}
                    </button>
                  )}
                  <button className="btn ghost" onClick={() => act("dismiss", `/queue/${it.email_id}/dismiss`)} disabled={!!busy}>
                    {busy === "dismiss" ? <Spinner dark /> : "Dismiss / Reject"}
                  </button>
                  {it.decision !== "ignore" && (
                    <button className="btn flare" onClick={() => act("flag", `/queue/${it.email_id}/flag`)} disabled={!!busy}>
                      {busy === "flag" ? <Spinner /> : "Flag hallucination"}
                    </button>
                  )}
                </div>
              ) : (
                <p className="panel-note" style={{ marginTop: 12 }}>Closed ({it.status}).</p>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

const TABS = [
  { key: "escalate", label: "Escalations", color: "var(--escalate)" },
  { key: "review", label: "Needs review", color: "var(--review)" },
  { key: "auto", label: "Auto", color: "var(--auto)" },
  { key: "audit", label: "Audit sample", color: "var(--accent)" },
  { key: "done", label: "Done", color: "var(--ignore)" },
];

export default function Review() {
  const { config, refresh } = useApp();
  const [items, setItems] = useState([]);
  const [counts, setCounts] = useState({});
  const [tab, setTab] = useState("escalate");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [flash, setFlash] = useState("");

  async function load() {
    setLoading(true);
    try {
      const [all, c] = await Promise.all([api.get("/queue"), api.get("/queue/counts")]);
      setItems(all); setCounts(c);
    } catch (e) { setError(e.message); }
    setLoading(false);
  }
  useEffect(() => { load(); }, []);

  async function onAction(path, body) {
    const r = await api.post(path, body);
    if (r?.detail) setFlash(`${r.detail}${r.label ? ` · feedback=${r.label}` : ""}`);
    await load();
    refresh();
  }

  const buckets = useMemo(() => ({
    escalate: items.filter((i) => i.decision === "escalate" && i.status === "pending"),
    review: items.filter((i) => i.decision === "review" && i.status === "pending"),
    auto: items.filter((i) => i.decision === "auto" && i.status === "pending" && !i.audit_sample),
    audit: items.filter((i) => i.decision === "auto" && i.status === "pending" && i.audit_sample),
    done: items.filter((i) => ["sent", "simulated", "dismissed"].includes(i.status)),
  }), [items]);

  const live = !!config?.live_send;
  const list = buckets[tab] || [];

  return (
    <PageFade>
      <Masthead index="03 / Act" eyebrow="Human review" title="Review Queue">
        Approve, edit, or dismiss suggested replies. Every action is saved as feedback.
        Auto-send is {live ? "LIVE" : "dry-run (nothing is actually sent)"}.
      </Masthead>

      <Scoreboard>
        <Score label="Escalations" value={counts.escalate ?? 0} count color="var(--escalate)" />
        <Score label="Needs review" value={counts.review ?? 0} count color="var(--review)" />
        <Score label="Auto pending" value={counts.auto ?? 0} count color="var(--auto)" />
        <Score label="Total pending" value={counts.pending_total ?? 0} count color="var(--ink)" />
      </Scoreboard>

      {flash && <Alert kind="ok">{flash}</Alert>}
      {error && <Alert kind="err">{error}</Alert>}

      <div className="tabs">
        {TABS.map((t) => (
          <button key={t.key} className={"tab" + (tab === t.key ? " active" : "")} onClick={() => setTab(t.key)}>
            {t.label}<span className="count">{buckets[t.key]?.length ?? 0}</span>
          </button>
        ))}
      </div>

      {loading ? (
        <div className="empty"><Spinner dark /> Loading queue…</div>
      ) : list.length === 0 ? (
        <div className="empty">Nothing here.</div>
      ) : (
        list.map((it) => (
          <Card key={it.email_id + it.status} it={it} audit={tab === "audit"} live={live} onAction={onAction} />
        ))
      )}
    </PageFade>
  );
}
