import { useState } from "react";
import { motion } from "framer-motion";
import { api } from "../api";
import { useApp } from "../App.jsx";
import { Alert, Field, Masthead, PageFade, Spinner } from "../components/ui.jsx";

const NO_ORDER = "";

function Metric({ label, value }) {
  const err = value === null || value === undefined;
  return (
    <div className="metric">
      <div className="m-label">{label}</div>
      <div className={"m-value" + (err ? " err" : "")}>{err ? "err" : value}</div>
      {!err && typeof value === "number" && (
        <div className="bar"><span style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }} /></div>
      )}
    </div>
  );
}

export default function Assistant() {
  const { orders, setup_required, company_data_version } = useApp();
  const [email, setEmail] = useState("");
  const [order, setOrder] = useState(NO_ORDER);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState(null);
  const [evalBusy, setEvalBusy] = useState(false);
  const [evalRes, setEvalRes] = useState(null);
  const [error, setError] = useState("");

  async function suggest() {
    setBusy(true); setError(""); setEvalRes(null);
    try {
      const r = await api.post("/assistant/suggest", {
        email,
        order_id: order || null,
        company_data_version: company_data_version || null,
      });
      setResult(r);
      if (r.detected_order_id) setOrder(r.detected_order_id);
    } catch (e) { setError(e.message); }
    setBusy(false);
  }

  async function checkAccuracy() {
    setEvalBusy(true);
    try {
      setEvalRes(await api.post("/assistant/evaluate", {
        email,
        order_id: result.order_id || null,
        gen: result.gen,
        company_data_version: result.company_data_version || company_data_version || null,
      }));
    } catch (e) { setError(e.message); }
    setEvalBusy(false);
  }

  const gen = result?.gen;
  const rem = gen?.remedy || {};

  return (
    <PageFade>
      <Masthead index="01 / Draft" eyebrow="Draft a reply" title="Reply Assistant">
        Paste a customer email to get a suggested reply grounded in your policy and past tickets. You can score it when you want.
      </Masthead>

      {setup_required && (
        <Alert kind="warn">
          Company data setup required — upload a policy and transactions in <b>Settings → Company Data</b> before drafting replies.
        </Alert>
      )}

      <div className="panel">
        <div className="panel-title">Incoming email <span className="kbd">INPUT</span></div>
        <Field label="Customer email">
          <textarea
            rows={7}
            value={email}
            placeholder="Paste the customer's email here…"
            onChange={(e) => setEmail(e.target.value)}
            disabled={!!setup_required}
          />
        </Field>
        <Field label="Order record">
          <select value={order} onChange={(e) => setOrder(e.target.value)} disabled={!!setup_required}>
            <option value="">Continue without a transaction record</option>
            {orders.map((o) => (
              <option key={o.order_id} value={o.order_id}>
                {o.order_id} · {o.product} (${o.price})
              </option>
            ))}
          </select>
        </Field>
        {result?.detected_order_id && (
          <Alert kind="info">Order <b className="mono">{result.detected_order_id}</b> was found in the email. Its transaction record is being used.</Alert>
        )}
        <div className="btn-row">
          <button className="btn primary" onClick={suggest} disabled={!email.trim() || busy || !!setup_required}>
            {busy ? <><Spinner /> Drafting…</> : "Suggest a reply"}
          </button>
        </div>
        {error && <Alert kind="err">{error}</Alert>}
      </div>

      {gen && (
        <motion.div initial={{ opacity: 0, y: 16 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.45 }}>
          <div className="section-eyebrow">Suggested reply</div>
          <div className="panel">
            <div className="reply-block">{gen.reply}</div>
            <dl className="kv">
              <dt>Remedy</dt><dd>{rem.remedy_type || "n/a"}</dd>
              <dt>Amount</dt><dd>{rem.remedy_amount ?? "n/a"}</dd>
              <dt>Rule</dt><dd>{rem.rule_cited || "n/a"}</dd>
              <dt>Escalate</dt><dd>{String(rem.escalate)}</dd>
            </dl>
          </div>

          <div className="section-eyebrow">Response quality (RAGAS)</div>
          <div className="panel">
            {!evalRes ? (
              <>
                <p className="panel-note">
                  Checks faithfulness, answer relevancy, and context precision against the policy chunks used for this reply. Runs only when you ask.
                </p>
                <button className="btn accent" onClick={checkAccuracy} disabled={evalBusy}>
                  {evalBusy ? <><Spinner /> Scoring…</> : "Check accuracy"}
                </button>
              </>
            ) : (
              <>
                <div className="metrics">
                  <Metric label="Faithfulness" value={evalRes.faithfulness} />
                  <Metric label="Answer relevancy" value={evalRes.answer_relevancy} />
                  <Metric label="Context precision" value={evalRes.context_precision} />
                  <Metric label="Routing quality" value={evalRes.quality_score} />
                </div>
                <dl className="kv">
                  <dt>Gated from AUTO</dt><dd>{String(evalRes.gated_from_auto)}</dd>
                  <dt>Disagreement</dt><dd>{String(evalRes.retrieval_disagreement)} (checked={String(evalRes.disagreement_checked)})</dd>
                  <dt>Flags</dt><dd>{evalRes.flags?.join(", ") || "none"}</dd>
                  <dt>Scoring error</dt><dd>{evalRes.scoring_error || "none"}</dd>
                </dl>
                {evalRes.faithfulness_details && Object.keys(evalRes.faithfulness_details).length > 0 && (
                  <details className="raw"><summary>Faithfulness claim details</summary>
                    <pre>{JSON.stringify(evalRes.faithfulness_details, null, 2)}</pre>
                  </details>
                )}
              </>
            )}
          </div>

          <div className="section-eyebrow">Grounding used</div>
          <div className="panel">
            <dl className="kv">
              <dt>Retrieved rules</dt><dd>{gen.retrieved_rule_ids?.join(", ") || "none"}</dd>
              <dt>Cited rules</dt><dd>{gen.cited_rule_ids?.join(", ") || "none"}</dd>
              <dt>Similar tickets</dt><dd>{gen.retrieved_similar_tickets?.join(", ") || "none"}</dd>
            </dl>
            <div className="field-label" style={{ marginTop: 16 }}>Policy clauses retrieved</div>
            {gen.retrieved_policy_chunks?.map((c, i) => (
              <div className="email-block" key={i} style={{ marginBottom: 8 }}>{c}</div>
            ))}
          </div>
        </motion.div>
      )}
    </PageFade>
  );
}
