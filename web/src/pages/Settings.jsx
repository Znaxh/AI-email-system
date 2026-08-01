import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useApp } from "../App.jsx";
import { Alert, Field, Masthead, PageFade, Spinner, Toggle } from "../components/ui.jsx";

function Section({ title, note, children }) {
  return (
    <div className="panel">
      <div className="panel-title">{title}</div>
      {note && <p className="panel-note">{note}</p>}
      {children}
    </div>
  );
}

function SaveBtn({ onClick, busy, children = "Save" }) {
  return (
    <button className="btn accent" onClick={onClick} disabled={busy}>
      {busy ? <Spinner /> : children}
    </button>
  );
}

export default function Settings() {
  const { refresh } = useApp();
  const [data, setData] = useState(null);
  const [cfg, setCfg] = useState({});
  const [error, setError] = useState("");
  const [flash, setFlash] = useState("");
  const [busy, setBusy] = useState("");
  const fileRef = useRef();

  async function load() {
    const d = await api.get("/settings");
    setData(d); setCfg(d.config);
  }
  useEffect(() => { load().catch((e) => setError(e.message)); }, []);

  function set(k, v) { setCfg((c) => ({ ...c, [k]: v })); }

  async function run(key, fn, msg) {
    setBusy(key); setError(""); setFlash("");
    try { await fn(); if (msg) setFlash(msg); await load(); refresh(); }
    catch (e) { setError(e.message); }
    setBusy("");
  }

  const saveConfig = (keys, msg) =>
    run(msg, () => api.post("/settings/config", { updates: Object.fromEntries(keys.map((k) => [k, cfg[k]])) }), msg);

  if (!data) return <PageFade><div className="empty"><Spinner dark /> Loading settings…</div></PageFade>;

  // -------- LLM step form state
  return (
    <PageFade>
      <Masthead index="04 / Configure" eyebrow="App configuration" title="Settings">
        Upload your company data, pick models, and set routing thresholds. Policy and transactions are required before generation works.
      </Masthead>

      {flash && <Alert kind="ok">{flash}</Alert>}
      {error && <Alert kind="err">{error}</Alert>}

      <CompanyDataSection
        data={data}
        busy={busy}
        run={run}
        setError={setError}
        setFlash={setFlash}
      />

      <Section title="Policy document (quick replace)" note={`Loaded: ${data.policy.filename} · ${data.policy.rules} indexed rules · categories: ${(data.policy.categories || []).join(", ") || "none"}`}>
        {data.policy.preview && <div className="email-block" style={{ marginBottom: 14 }}>{data.policy.preview.slice(0, 600)}</div>}
        <input ref={fileRef} type="file" accept=".pdf,.docx,.md,.txt" style={{ marginBottom: 12 }} />
        <div>
          <SaveBtn busy={busy === "policy"} onClick={() => {
            const f = fileRef.current?.files?.[0];
            if (!f) { setError("Choose a file first."); return; }
            const form = new FormData(); form.append("file", f);
            run("policy", () => api.postForm("/settings/policy", form), "Policy replaced and re-indexed.");
          }}>Replace policy & re-index</SaveBtn>
        </div>
      </Section>

      <ExampleReplies data={data} onSave={(rows) => run("examples", () => api.post("/settings/examples", { examples: rows }), "Examples saved.")} busy={busy === "examples"} />

      <Section title="Policy retrieval" note="How policy rules are found for each email. Cross-encoder rerank is optional and slower.">
        <label className="field"><Toggle checked={!!cfg.use_embeddings} onChange={(v) => set("use_embeddings", v)} label="Use local embeddings" /></label>
        <div className="row">
          <Field label="RRF k"><input type="number" value={cfg.rrf_k} onChange={(e) => set("rrf_k", Number(e.target.value))} style={{ width: 120 }} /></Field>
          <Field label="Top-k policy rules"><input type="number" value={cfg.k_policy} onChange={(e) => set("k_policy", Number(e.target.value))} style={{ width: 120 }} /></Field>
        </div>
        <label className="field"><Toggle checked={!!cfg.cross_encoder_rerank} onChange={(v) => set("cross_encoder_rerank", v)} label="Cross-encoder rerank (optional)" /></label>
        <label className="field"><Toggle checked={!!cfg.policy_llm_chunking} onChange={(v) => set("policy_llm_chunking", v)} label="LLM fallback for unstructured sections" /></label>
        <SaveBtn busy={busy === "retrieval"} onClick={() => saveConfig(["use_embeddings", "rrf_k", "k_policy", "cross_encoder_rerank", "policy_llm_chunking"], "retrieval")}>Save retrieval settings</SaveBtn>
      </Section>

      <LLMSection data={data} run={run} busy={busy} />

      <Section title="Evaluation gates" note="Controls when a reply can go AUTO. Lower faithfulness blocks auto-send.">
        <Field label={`Faithfulness gate (block AUTO below): ${Number(cfg.faithfulness_gate).toFixed(2)}`}>
          <input type="range" min={0} max={1} step={0.05} value={cfg.faithfulness_gate} onChange={(e) => set("faithfulness_gate", Number(e.target.value))} />
        </Field>
        <Field label={`Disagreement check sample rate: ${Number(cfg.retrieval_disagreement_sample_rate).toFixed(2)}`}>
          <input type="range" min={0} max={1} step={0.05} value={cfg.retrieval_disagreement_sample_rate} onChange={(e) => set("retrieval_disagreement_sample_rate", Number(e.target.value))} />
        </Field>
        <Field label={`AUTO audit sample rate: ${Number(cfg.audit_sample_rate).toFixed(2)}`}>
          <input type="range" min={0} max={1} step={0.01} value={cfg.audit_sample_rate} onChange={(e) => set("audit_sample_rate", Number(e.target.value))} />
        </Field>
        <SaveBtn busy={busy === "gates"} onClick={() => saveConfig(["faithfulness_gate", "retrieval_disagreement_sample_rate", "audit_sample_rate"], "gates")}>Save evaluation gates</SaveBtn>
      </Section>

      <Section title="Email connection" note="demo is offline (empty inbox, dry-run sends). mcp connects to a live mailbox via an MCP server.">
        <Field label="Email source">
          <select value={cfg.email_source} onChange={(e) => set("email_source", e.target.value)}>
            <option value="demo">demo (offline)</option><option value="mcp">mcp</option>
          </select>
        </Field>
        <div className="btn-row">
          <SaveBtn busy={busy === "email"} onClick={() => saveConfig(["email_source"], "email")}>Save email source</SaveBtn>
          <button className="btn ghost" disabled={busy === "test-inbox"} onClick={() => run("test-inbox", async () => {
            const r = await api.post("/settings/test/inbox"); setFlash((r.ok ? "Inbox OK: " : "Inbox failed: ") + r.detail);
          })}>{busy === "test-inbox" ? <Spinner dark /> : "Test inbox connection"}</button>
        </div>
      </Section>

      <Section title="Automation thresholds" note="Confidence is 0 to 100. At or above T1 can go AUTO. Below T2 escalates. In between, it goes to review.">
        <Field label={`T1 (auto-reply at or above): ${Math.round(cfg.t1)}`}>
          <input type="range" min={0} max={100} value={cfg.t1} onChange={(e) => set("t1", Number(e.target.value))} />
        </Field>
        <Field label={`T2 (escalate below): ${Math.round(cfg.t2)}`}>
          <input type="range" min={0} max={100} value={cfg.t2} onChange={(e) => set("t2", Number(e.target.value))} />
        </Field>
        <label className="field"><Toggle checked={!!cfg.live_send} onChange={(v) => set("live_send", v)} label="Live send (actually dispatch auto-replies)" /></label>
        <SaveBtn busy={busy === "auto"} onClick={() => {
          if (cfg.t2 >= cfg.t1) { setError("T2 must be below T1."); return; }
          saveConfig(["t1", "t2", "live_send"], "auto");
        }}>Save automation settings</SaveBtn>
      </Section>

      <Section title="Notifications" note="Optionally email a digest of pending review items after each sync.">
        <label className="field"><Toggle checked={!!cfg.digest_enabled} onChange={(v) => set("digest_enabled", v)} label="Email a digest after each inbox sync" /></label>
        <Field label="Digest recipient email"><input type="email" value={cfg.digest_recipient || ""} onChange={(e) => set("digest_recipient", e.target.value)} /></Field>
        <div className="btn-row">
          <SaveBtn busy={busy === "notify"} onClick={() => saveConfig(["digest_enabled", "digest_recipient"], "notify")}>Save notifications</SaveBtn>
          <button className="btn ghost" disabled={busy === "digest"} onClick={() => run("digest", async () => {
            const d = await api.post("/notify/digest"); setFlash(d.detail);
          })}>{busy === "digest" ? <Spinner dark /> : "Send digest now"}</button>
        </div>
      </Section>

      <StorageSection cfg={cfg} set={set} saveConfig={saveConfig} run={run} busy={busy} setFlash={setFlash} />
    </PageFade>
  );
}

// ---------------------------------------------------------------- LLM by step
function LLMSection({ data, run, busy }) {
  const [step, setStep] = useState("generate");
  const cur = step === "generate"
    ? { provider: data.llm.gen_provider, model: data.llm.gen_model }
    : { provider: data.llm.cls_provider, model: data.llm.cls_model };
  const [provider, setProvider] = useState(cur.provider);
  const [model, setModel] = useState(cur.model);
  const [key, setKey] = useState("");
  useEffect(() => { setProvider(cur.provider); setModel(cur.model); setKey(""); }, [step]); // eslint-disable-line

  return (
    <Section title="LLM models" note="Generation drafts replies and scores them. Categorization sorts inbound mail. Leave the API key blank to keep the one you already saved.">
      <div className="tabs" style={{ marginBottom: 18 }}>
        <button className={"tab" + (step === "generate" ? " active" : "")} onClick={() => setStep("generate")}>Email generation</button>
        <button className={"tab" + (step === "classify" ? " active" : "")} onClick={() => setStep("classify")}>Email categorization</button>
      </div>
      <div className="row" style={{ gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
        {data.providers.map((p) => (
          <span className="chip" key={p.name}>{p.name}: {p.configured ? "configured ✓" : "no key"}{p.used_for.length ? ` · ${p.used_for.join(", ")}` : ""}</span>
        ))}
      </div>
      <Field label="Provider">
        <select value={provider} onChange={(e) => setProvider(e.target.value)}>
          {data.providers.map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
        </select>
      </Field>
      <Field label="Model (blank = provider default)"><input type="text" value={model} onChange={(e) => setModel(e.target.value)} placeholder={data.llm.default_models?.[provider] || ""} /></Field>
      <Field label="API key (blank keeps existing)"><input type="password" value={key} onChange={(e) => setKey(e.target.value)} /></Field>
      <div className="btn-row">
        <button className="btn accent" disabled={busy === "llm"} onClick={() => run("llm", () => api.post("/settings/llm", { step, provider, model, api_key: key }), `Saved. ${step} now uses ${provider}.`)}>
          {busy === "llm" ? <Spinner /> : "Save model"}
        </button>
        <button className="btn ghost" disabled={busy === "test-llm"} onClick={() => run("test-llm", async () => {
          const r = await api.post(`/settings/test/${step}`);
          if (!r.ok) throw new Error(r.detail);
        }, "Connection OK.")}>{busy === "test-llm" ? <Spinner dark /> : "Test connection"}</button>
      </div>
    </Section>
  );
}

// ---------------------------------------------------------- example replies
function ExampleReplies({ data, onSave, busy }) {
  const [rows, setRows] = useState(data.examples || []);
  const [email, setEmail] = useState(""); const [reply, setReply] = useState(""); const [oid, setOid] = useState("");
  useEffect(() => { setRows(data.examples || []); }, [data.examples]);

  function add() {
    if (!email.trim() || !reply.trim()) return;
    const next = [...rows, { incoming_email: email.trim(), actual_reply: reply.trim(), ...(oid.trim() ? { order_id: oid.trim() } : {}) }];
    setRows(next); setEmail(""); setReply(""); setOid(""); onSave(next);
  }
  function del(i) { const next = rows.filter((_, j) => j !== i); setRows(next); onSave(next); }

  return (
    <Section title="Example replies" note="Past email and reply pairs used to match your writing style. They are not used in batch evaluation.">
      <Field label="Customer email"><textarea rows={3} value={email} onChange={(e) => setEmail(e.target.value)} /></Field>
      <Field label="Agent reply sent"><textarea rows={3} value={reply} onChange={(e) => setReply(e.target.value)} /></Field>
      <div className="row">
        <Field label="Order ID (optional)"><input type="text" value={oid} onChange={(e) => setOid(e.target.value)} style={{ width: 180 }} /></Field>
        <button className="btn accent" style={{ alignSelf: "center" }} onClick={add} disabled={busy || !email.trim() || !reply.trim()}>{busy ? <Spinner /> : "Add example"}</button>
      </div>
      {rows.length > 0 ? (
        <div style={{ marginTop: 16 }}>
          {rows.map((r, i) => (
            <div className="row" key={i} style={{ borderTop: "1px solid var(--line-2)", padding: "10px 0", alignItems: "flex-start" }}>
              <span className="mono" style={{ minWidth: 52 }}>{r.ticket_id || `#${i + 1}`}</span>
              <span className="grow muted" style={{ fontSize: ".82rem" }}>{(r.incoming_email || "").slice(0, 110)}</span>
              <button className="btn sm ghost" onClick={() => del(i)}>Delete</button>
            </div>
          ))}
        </div>
      ) : <p className="panel-note" style={{ marginTop: 12 }}>No user-supplied examples yet.</p>}
    </Section>
  );
}

// ---------------------------------------------------------------- storage
function StorageSection({ cfg, set, saveConfig, run, busy, setFlash }) {
  const [secrets, setSecrets] = useState({});
  const ss = (k, v) => setSecrets((s) => ({ ...s, [k]: v }));
  return (
    <Section title="Storage" note="Where queue, feedback, and scores are stored. Local needs no setup. Cloud options are optional; secrets stay in .env.">
      <div className="row">
        <Field label="Structured store">
          <select value={cfg.storage_structured_provider} onChange={(e) => set("storage_structured_provider", e.target.value)}>
            <option value="local">local</option><option value="postgres">postgres</option>
          </select>
        </Field>
        <Field label="Blob store">
          <select value={cfg.storage_blob_provider} onChange={(e) => set("storage_blob_provider", e.target.value)}>
            {["local", "s3", "azure", "gcs", "postgres"].map((o) => <option key={o} value={o}>{o}</option>)}
          </select>
        </Field>
      </div>
      <Field label="S3 bucket"><input type="text" value={cfg.storage_s3_bucket || ""} onChange={(e) => set("storage_s3_bucket", e.target.value)} /></Field>
      <Field label="Postgres DSN (blank keeps existing)"><input type="password" onChange={(e) => ss("STORAGE_POSTGRES_DSN", e.target.value)} /></Field>
      <div className="btn-row">
        <button className="btn accent" disabled={busy === "storage"} onClick={() => run("storage", async () => {
          await api.post("/settings/config", { updates: {
            storage_structured_provider: cfg.storage_structured_provider,
            storage_blob_provider: cfg.storage_blob_provider,
            storage_s3_bucket: (cfg.storage_s3_bucket || "").trim(),
          } });
          const env = { STORAGE_STRUCTURED_PROVIDER: cfg.storage_structured_provider, STORAGE_BLOB_PROVIDER: cfg.storage_blob_provider };
          for (const [k, v] of Object.entries(secrets)) if (v && v.trim()) env[k] = v.trim();
          await api.post("/settings/env", { updates: env });
        }, "Storage settings saved.")}>{busy === "storage" ? <Spinner /> : "Save storage settings"}</button>
        <button className="btn ghost" disabled={busy === "test-storage"} onClick={() => run("test-storage", async () => {
          const r = await api.post("/settings/test/storage"); setFlash((r.ok ? "Storage OK: " : "Storage failed: ") + r.detail);
        })}>{busy === "test-storage" ? <Spinner dark /> : "Test storage connection"}</button>
      </div>
    </Section>
  );
}

// ---------------------------------------------------------- company data setup
const DATE_ORDERS = ["DMY", "MDY", "YMD"];
const MONEY_STYLES = [
  { value: "dot_decimal", label: "1,234.56 (dot decimal)" },
  { value: "comma_decimal", label: "1.234,56 (comma decimal)" },
];

function emptyAsset() {
  return { token: "", fileHash: "", filename: "", preview: null, mapping: { fields: {}, date_orders: {}, money_styles: {}, sheet: null }, dryRun: null };
}

function CompanyDataSection({ data, busy, run, setError, setFlash }) {
  const cd = data.company_data || {};
  const canonical = data.canonical_fields || { transactions: [], tickets: [] };
  const [assets, setAssets] = useState({
    policy: emptyAsset(),
    transactions: emptyAsset(),
    tickets: emptyAsset(),
  });
  const [confirmDegraded, setConfirmDegraded] = useState(false);
  const [rollbackId, setRollbackId] = useState("");

  function patch(target, patchObj) {
    setAssets((a) => ({ ...a, [target]: { ...a[target], ...patchObj } }));
  }

  async function stage(target, file) {
    if (!file) return;
    const form = new FormData();
    form.append("file", file);
    const staged = await api.postForm(`/settings/company-data/stage?target=${target}`, form);
    const preview = target === "policy"
      ? { columns: [], samples: [], suggested_mapping: { fields: {}, date_orders: {}, money_styles: {} }, sheets: [], row_count: 0 }
      : await api.get(`/settings/company-data/preview/${staged.token}`);
    const mapping = preview.suggested_mapping || { fields: {}, date_orders: {}, money_styles: {}, sheet: preview.sheet || null };
    patch(target, {
      token: staged.token,
      fileHash: staged.file_hash,
      filename: staged.filename,
      preview,
      mapping,
      dryRun: null,
    });
    return staged;
  }

  async function dryRun(target) {
    const a = assets[target];
    if (!a.token) throw new Error(`Stage a ${target} file first.`);
    const result = await api.post(`/settings/company-data/dry-run/${a.token}`, {
      mapping: a.mapping,
      file_hash: a.fileHash,
    });
    patch(target, { dryRun: result });
    return result;
  }

  function setField(target, canon, src) {
    const mapping = {
      ...assets[target].mapping,
      fields: { ...(assets[target].mapping.fields || {}), [canon]: src || null },
    };
    patch(target, { mapping, dryRun: null });
  }

  function setConvention(target, kind, canon, value) {
    const key = kind === "date" ? "date_orders" : "money_styles";
    const mapping = {
      ...assets[target].mapping,
      [key]: { ...(assets[target].mapping[key] || {}), [canon]: value || undefined },
    };
    patch(target, { mapping, dryRun: null });
  }

  const txnVerdict = assets.transactions.dryRun?.verdict;
  const needsConfirm = txnVerdict === "DEGRADED";

  return (
    <Section
      title="Company data"
      note={
        cd.setup_required
          ? "Setup required: upload a policy document and a transactions file, map columns, dry-run, then activate. Sample files for local trials: tests/fixtures/northpeak/."
          : `Active version ${cd.active_version || "—"} · ${(cd.advisories || []).join(" · ") || "ready"}`
      }
    >
      {(cd.capability_impact || []).length > 0 && (
        <Alert kind="err">Dataset gaps (not routing flags): {(cd.capability_impact || []).join("; ")}</Alert>
      )}
      {(cd.advisories || []).filter((a) => a !== "setup_required").map((a) => (
        <p className="panel-note" key={a}>{a}</p>
      ))}

      {["policy", "transactions", "tickets"].map((target) => {
        const a = assets[target];
        const fields = target === "policy" ? [] : (canonical[target] || []);
        const accept = target === "policy" ? ".pdf,.docx,.md,.txt" : ".csv,.tsv,.xlsx,.json";
        return (
          <div key={target} style={{ borderTop: "1px solid var(--line-2)", paddingTop: 14, marginTop: 14 }}>
            <div className="panel-title" style={{ fontSize: ".9rem" }}>
              {target}{target === "tickets" ? " (optional)" : " (required)"}
            </div>
            <input
              type="file"
              accept={accept}
              style={{ marginBottom: 8 }}
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                run(`stage-${target}`, () => stage(target, f), `${target} staged.`);
              }}
            />
            {a.filename && <p className="panel-note">Staged: {a.filename} · hash {String(a.fileHash || "").slice(0, 12)}…</p>}

            {a.preview?.sheets?.length > 1 && (
              <Field label="Sheet">
                <select
                  value={a.mapping.sheet || a.preview.sheets[0]}
                  onChange={(e) => run(`sheet-${target}`, async () => {
                    const preview = await api.get(`/settings/company-data/preview/${a.token}?sheet=${encodeURIComponent(e.target.value)}`);
                    patch(target, {
                      preview,
                      mapping: { ...preview.suggested_mapping, sheet: e.target.value },
                      dryRun: null,
                    });
                  })}
                >
                  {a.preview.sheets.map((s) => <option key={s} value={s}>{s}</option>)}
                </select>
              </Field>
            )}

            {fields.length > 0 && a.preview && (
              <div style={{ marginTop: 8 }}>
                <p className="panel-note">Map each required field to a column in your file. Suggestions are defaults only.</p>
                {fields.map((canon) => (
                  <div className="row" key={canon} style={{ gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
                    <Field label={canon}>
                      <select
                        value={a.mapping.fields?.[canon] || ""}
                        onChange={(e) => setField(target, canon, e.target.value)}
                        style={{ minWidth: 180 }}
                      >
                        <option value="">— unmapped —</option>
                        {(a.preview.columns || []).map((c) => <option key={c} value={c}>{c}</option>)}
                      </select>
                    </Field>
                    {["order_date", "delivery_date", "promised_delivery_date"].includes(canon) && a.mapping.fields?.[canon] && (
                      <Field label="Date order">
                        <select
                          value={a.mapping.date_orders?.[canon] || ""}
                          onChange={(e) => setConvention(target, "date", canon, e.target.value)}
                        >
                          <option value="">auto / choose if blocked</option>
                          {DATE_ORDERS.map((o) => <option key={o} value={o}>{o}</option>)}
                        </select>
                      </Field>
                    )}
                    {canon === "price" && a.mapping.fields?.[canon] && (
                      <Field label="Money style">
                        <select
                          value={a.mapping.money_styles?.[canon] || ""}
                          onChange={(e) => setConvention(target, "money", canon, e.target.value)}
                        >
                          <option value="">auto / choose if blocked</option>
                          {MONEY_STYLES.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                        </select>
                      </Field>
                    )}
                  </div>
                ))}
              </div>
            )}

            {target !== "policy" && a.token && (
              <button className="btn ghost" style={{ marginTop: 8 }} disabled={busy === `dry-${target}`} onClick={() => run(`dry-${target}`, () => dryRun(target), `${target} dry-run complete.`)}>
                {busy === `dry-${target}` ? <Spinner dark /> : "Dry-run validate"}
              </button>
            )}
            {target === "policy" && a.token && (
              <button className="btn ghost" style={{ marginTop: 8 }} disabled={busy === `dry-${target}`} onClick={() => run(`dry-${target}`, () => dryRun(target), "Policy dry-run complete.")}>
                {busy === `dry-${target}` ? <Spinner dark /> : "Dry-run validate"}
              </button>
            )}

            {a.dryRun && (
              <div style={{ marginTop: 10 }}>
                <span className="chip">verdict: {a.dryRun.verdict}</span>
                {(a.dryRun.capability_impact || []).map((c) => <span className="chip" key={c}>{c}</span>)}
                {(a.dryRun.advisories || []).map((c) => <span className="chip" key={c}>{c}</span>)}
                {(a.dryRun.issues || []).slice(0, 8).map((iss, i) => (
                  <p className="panel-note" key={i}>{iss.level}: {iss.message}{iss.row != null ? ` (row ${iss.row})` : ""}</p>
                ))}
              </div>
            )}
          </div>
        );
      })}

      {needsConfirm && (
        <label className="field" style={{ marginTop: 14 }}>
          <Toggle checked={confirmDegraded} onChange={setConfirmDegraded} label="I understand this DEGRADED upload will activate with limited capabilities (AUTO still possible for unaffected emails)" />
        </label>
      )}

      <div className="btn-row" style={{ marginTop: 16 }}>
        <SaveBtn
          busy={busy === "activate"}
          onClick={() => {
            if (!assets.policy.token || !assets.transactions.token) {
              setError("Policy and transactions are required.");
              return;
            }
            if (needsConfirm && !confirmDegraded) {
              setError("Confirm DEGRADED activation to continue.");
              return;
            }
            run("activate", async () => {
              // Ensure dry-runs exist
              if (!assets.policy.dryRun) await dryRun("policy");
              if (!assets.transactions.dryRun) await dryRun("transactions");
              if (assets.tickets.token && !assets.tickets.dryRun) await dryRun("tickets");
              const body = {
                policy: { token: assets.policy.token, mapping: {}, file_hash: assets.policy.fileHash },
                transactions: {
                  token: assets.transactions.token,
                  mapping: assets.transactions.mapping,
                  file_hash: assets.transactions.fileHash,
                },
                confirm_degraded: confirmDegraded || assets.transactions.dryRun?.verdict === "READY",
              };
              if (assets.tickets.token) {
                body.tickets = {
                  token: assets.tickets.token,
                  mapping: assets.tickets.mapping,
                  file_hash: assets.tickets.fileHash,
                };
              }
              await api.post("/settings/company-data/activate", body);
              setAssets({ policy: emptyAsset(), transactions: emptyAsset(), tickets: emptyAsset() });
              setConfirmDegraded(false);
            }, "Company data activated.");
          }}
        >
          Activate company data
        </SaveBtn>
      </div>

      {(cd.versions || []).length > 0 && (
        <div style={{ marginTop: 18 }}>
          <Field label="Rollback to previous version">
            <select value={rollbackId} onChange={(e) => setRollbackId(e.target.value)}>
              <option value="">— select version —</option>
              {(cd.versions || []).map((v) => (
                <option key={v} value={v}>{v}{v === cd.active_version ? " (active)" : ""}</option>
              ))}
            </select>
          </Field>
          <button
            className="btn ghost"
            disabled={!rollbackId || rollbackId === cd.active_version || busy === "rollback"}
            onClick={() => run("rollback", () => api.post("/settings/company-data/rollback", { version_id: rollbackId }), "Rolled back.")}
          >
            {busy === "rollback" ? <Spinner dark /> : "Rollback (rebuild-verify-swap)"}
          </button>
        </div>
      )}
    </Section>
  );
}

