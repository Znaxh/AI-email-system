import { useState } from "react";
import { motion } from "framer-motion";
import { api } from "../api";
import { useApp } from "../App.jsx";
import { Alert, DecisionChip, Field, Masthead, PageFade, Score, Scoreboard, Spinner } from "../components/ui.jsx";

export default function Inbox() {
  const { config, refresh, setup_required } = useApp();
  const [limit, setLimit] = useState(20);
  const [syncBusy, setSyncBusy] = useState(false);
  const [summary, setSummary] = useState(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [routeBusy, setRouteBusy] = useState(false);
  const [routed, setRouted] = useState(null);
  const [error, setError] = useState("");

  async function sync() {
    setSyncBusy(true); setError(""); setSummary(null);
    try {
      const r = await api.post("/inbox/sync", { limit: Number(limit) });
      setSummary(r);
      refresh();
    } catch (e) { setError(e.message); }
    setSyncBusy(false);
  }

  async function routeOne() {
    setRouteBusy(true); setError(""); setRouted(null);
    try {
      setRouted(await api.post("/inbox/route-one", { subject, body }));
      refresh();
    } catch (e) { setError(e.message); }
    setRouteBusy(false);
  }

  return (
    <PageFade>
      <Masthead index="02 / Intake" eyebrow="Bring mail in" title="Inbox">
        Sync unread mail from your connected source, or paste one email. Each message is classified and queued for review.
      </Masthead>

      {setup_required && (
        <Alert kind="warn">
          Company data setup required — upload a policy and transactions in <b>Settings → Company Data</b> before routing mail.
        </Alert>
      )}

      <div className="panel">
        <div className="panel-title">Sync inbox <span className="kbd">{config?.email_source || "n/a"}</span></div>
        <p className="panel-note">
          Auto-send is <b>{config?.live_send ? "LIVE" : "dry-run"}</b> · thresholds T1={config?.t1} / T2={config?.t2}.
          Change these in Settings.
        </p>
        <div className="row">
          <Field label="Max messages">
            <input type="number" min={1} max={100} value={limit}
              onChange={(e) => setLimit(e.target.value)} style={{ width: 120 }} disabled={!!setup_required} />
          </Field>
          <button className="btn primary" onClick={sync} disabled={syncBusy || !!setup_required} style={{ alignSelf: "center" }}>
            {syncBusy ? <><Spinner /> Routing…</> : "Sync inbox now"}
          </button>
        </div>
      </div>

      {summary && (
        <motion.div initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
          <Scoreboard>
            <Score label="Fetched" value={summary.fetched} count color="var(--ink)" />
            <Score label="Auto-reply" value={summary.tally.auto} count color="var(--auto)" />
            <Score label="Needs review" value={summary.tally.review} count color="var(--review)" />
            <Score label="Escalated" value={summary.tally.escalate} count color="var(--escalate)" />
            <Score label="Ignored" value={summary.tally.ignore} count color="var(--ignore)" />
          </Scoreboard>
          {summary.fetched === 0 ? (
            <Alert kind="info">No messages fetched.</Alert>
          ) : (
            <Alert kind="ok">Routed and queued. Open the <b>Review</b> page to act on them.</Alert>
          )}
          {summary.failed?.length > 0 && (
            <Alert kind="warn">
              {summary.failed.length} email(s) failed and will retry next sync (dead-lettered after {summary.max_attempts} attempts):{" "}
              {summary.failed.map((f) => `${f.email_id} (${f.status})`).join(", ")}
            </Alert>
          )}
          {summary.digest && <Alert kind="info">Digest: {summary.digest.detail}</Alert>}
        </motion.div>
      )}

      <div className="section-eyebrow">Route a single email</div>
      <div className="panel">
        <p className="panel-note">Paste one incoming email to run it through the exact same pipeline.</p>
        <Field label="Subject (optional)">
          <input type="text" value={subject} onChange={(e) => setSubject(e.target.value)} />
        </Field>
        <Field label="Email body">
          <textarea rows={6} value={body} onChange={(e) => setBody(e.target.value)} />
        </Field>
        <button className="btn accent" onClick={routeOne} disabled={!body.trim() || routeBusy || !!setup_required}>
          {routeBusy ? <><Spinner /> Routing…</> : "Route this email"}
        </button>
      </div>

      {error && <Alert kind="err">{error}</Alert>}

      {routed && (
        <motion.div className="panel" initial={{ opacity: 0, y: 12 }} animate={{ opacity: 1, y: 0 }}>
          <div className="row" style={{ justifyContent: "space-between" }}>
            <DecisionChip decision={routed.decision} />
            <span className="mono muted">confidence {routed.confidence}/100</span>
          </div>
          {routed.judge && Object.keys(routed.judge).length > 0 && (
            <dl className="kv">
              <dt>Cited rule</dt><dd>{routed.judge.cited_rule || "n/a"}</dd>
              <dt>Escalate</dt><dd>{String(routed.judge.escalate)}{routed.judge.escalate_reason ? `: ${routed.judge.escalate_reason}` : ""}</dd>
              <dt>Flags</dt><dd>{routed.flags?.join(", ") || "none"}</dd>
            </dl>
          )}
          {routed.suggested_reply && (
            <>
              <div className="field-label" style={{ marginTop: 12 }}>Suggested reply</div>
              <div className="reply-block">{routed.suggested_reply}</div>
            </>
          )}
          <p className="panel-note" style={{ marginTop: 14 }}>Queued. Open Review to manage it.</p>
        </motion.div>
      )}
    </PageFade>
  );
}
