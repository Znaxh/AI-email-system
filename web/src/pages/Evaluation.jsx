import { useEffect, useState } from "react";
import { api } from "../api";
import { Alert, CountUp, Masthead, PageFade, Score, Scoreboard, Spinner } from "../components/ui.jsx";

function Rate({ label, payload }) {
  const p = payload || {};
  const n = p.n || 0;
  if (p.insufficient_data || p.rate === null || p.rate === undefined) {
    return (
      <div className="metric">
        <div className="m-label">{label}</div>
        <div className="m-value err">insufficient</div>
        <div className="score-sub">n = {n} (need ≥ 20)</div>
      </div>
    );
  }
  return (
    <div className="metric">
      <div className="m-label">{label}</div>
      <div className="m-value">{(p.rate * 100).toFixed(1)}%</div>
      <div className="score-sub">
        n = {n} · 95% CI [{p.ci_low?.toFixed?.(3)}, {p.ci_high?.toFixed?.(3)}]
      </div>
    </div>
  );
}

function RateTable({ rows }) {
  if (!rows || rows.length === 0) return <div className="empty">No data.</div>;
  const cols = Object.keys(rows[0]);
  return (
    <div className="table-wrap">
      <table className="data">
        <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>{cols.map((c) => <td key={c}>{fmt(r[c])}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
function fmt(v) {
  if (v === null || v === undefined) return "n/a";
  if (typeof v === "number") return Number.isInteger(v) ? v : v.toFixed(3);
  if (typeof v === "boolean") return String(v);
  return v;
}

export default function Evaluation() {
  const [tab, setTab] = useState("quality");
  const [quality, setQuality] = useState(null);
  const [rel, setRel] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const [q, r] = await Promise.all([
          api.get("/evaluation/quality"),
          api.get("/evaluation/reliability"),
        ]);
        setQuality(q); setRel(r);
      } catch (e) { setError(e.message); }
      setLoading(false);
    })();
  }, []);

  const avg = quality?.averages;
  const byCat = rel?.critical_error_by_category
    ? Object.entries(rel.critical_error_by_category).map(([category, p]) => ({
        category, rate: p.rate, n: p.n, ci_low: p.ci_low, ci_high: p.ci_high,
      }))
    : [];

  return (
    <PageFade>
      <Masthead index="05 / Measure" eyebrow="Quality and reliability" title="Evaluation">
        Response quality is scored automatically with RAGAS. Reliability comes from human review feedback. Each rate shows its sample size and a 95% confidence interval.
      </Masthead>

      <div className="tabs">
        <button className={"tab" + (tab === "quality" ? " active" : "")} onClick={() => setTab("quality")}>Response Quality</button>
        <button className={"tab" + (tab === "reliability" ? " active" : "")} onClick={() => setTab("reliability")}>System Reliability</button>
      </div>

      {error && <Alert kind="err">{error}</Alert>}
      {loading ? (
        <div className="empty"><Spinner dark /> Loading…</div>
      ) : tab === "quality" ? (
        !avg ? (
          <Alert kind="warn">No RAGAS scores yet. Run <span className="mono">python pipeline.py --all</span> or route live mail.</Alert>
        ) : (
          <>
            <Scoreboard>
              <Score label="Faithfulness" value={avg.faithfulness ?? "n/a"} decimals={3} count={avg.faithfulness != null} color="var(--accent)" sub={`n=${avg.n_faithfulness}`} />
              <Score label="Answer relevancy" value={avg.answer_relevancy ?? "n/a"} decimals={3} count={avg.answer_relevancy != null} color="var(--accent)" sub={`n=${avg.n_answer_relevancy}`} />
              <Score label="Context precision" value={avg.context_precision ?? "n/a"} decimals={3} count={avg.context_precision != null} color="var(--accent)" sub={`n=${avg.n_context_precision}`} />
              <Score label="Gated from AUTO" value={`${avg.gated} / ${avg.total}`} color="var(--review)" />
            </Scoreboard>

            <div className="section-eyebrow">Per-response detail</div>
            {quality.rows.map((r, i) => (
              <details className="raw panel" key={i} style={{ padding: 16 }}>
                <summary>
                  {(r.ticket_id || r.response_id || "?")} · faith={r.faithfulness} · relev={r.answer_relevancy} · prec={r.context_precision} · gated={String(r.gated_from_auto)}
                </summary>
                <pre>{JSON.stringify({
                  faithfulness: r.faithfulness, answer_relevancy: r.answer_relevancy,
                  context_precision: r.context_precision, quality_score: r.quality_score,
                  retrieval_disagreement: r.retrieval_disagreement, disagreement_checked: r.disagreement_checked,
                  gated_from_auto: r.gated_from_auto, scoring_error: r.scoring_error, flags: r.flags,
                }, null, 2)}</pre>
              </details>
            ))}
          </>
        )
      ) : (
        <>
          <div className="section-eyebrow">Critical error rate (labeled AUTO only)</div>
          <div className="metrics">
            <Rate label="Critical error rate" payload={rel.critical_error_rate} />
            <Rate label="Overall reliability" payload={rel.reliability_rate} />
            <Rate label="Escalation miss" payload={rel.escalation_miss_rate} />
          </div>
          <p className="panel-note" style={{ marginTop: 12 }}>
            Audit coverage: {rel.audit_coverage?.labeled_auto ?? 0} labeled AUTO / {rel.audit_coverage?.all_auto ?? 0} total AUTO in queue.
            Total feedback events: <CountUp value={rel.n_feedback_events ?? 0} />.
          </p>

          <div className="section-eyebrow">By category</div>
          <RateTable rows={byCat} />

          <div className="section-eyebrow">Calibration (quality score vs acceptance)</div>
          {rel.calibration?.buckets?.length ? (
            <RateTable rows={rel.calibration.buckets} />
          ) : (
            <Alert kind="info">Need feedback events with joined RAGAS scores for calibration.</Alert>
          )}

          {rel.weekly_reliability?.length > 0 && (
            <>
              <div className="section-eyebrow">Weekly trend</div>
              <RateTable rows={rel.weekly_reliability} />
            </>
          )}
        </>
      )}
    </PageFade>
  );
}
