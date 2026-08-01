import { motion, useMotionValue, useTransform, animate } from "framer-motion";
import { useEffect, useState } from "react";

// ---- page masthead (the scoreboard signature) -----------------------------
export function Masthead({ index, eyebrow, title, children }) {
  const words = title.split(" ");
  return (
    <div className="masthead">
      <div className="masthead-eyebrow">{eyebrow}</div>
      <div className="masthead-row">
        <span className="masthead-index">{index}</span>
        <h1 aria-label={title}>
          {words.map((w, i) => (
            <span className="mask" key={i}>
              <motion.span
                style={{ display: "inline-block", marginRight: "0.28em" }}
                initial={{ y: "110%" }}
                animate={{ y: 0 }}
                transition={{ delay: 0.05 + i * 0.07, duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
              >
                {w}
              </motion.span>
            </span>
          ))}
        </h1>
      </div>
      {children && (
        <motion.p
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.35, duration: 0.5 }}
        >
          {children}
        </motion.p>
      )}
    </div>
  );
}

// ---- animated count-up number ---------------------------------------------
export function CountUp({ value, decimals = 0 }) {
  const mv = useMotionValue(0);
  const rounded = useTransform(mv, (v) =>
    decimals ? v.toFixed(decimals) : Math.round(v).toString()
  );
  const [text, setText] = useState(decimals ? (0).toFixed(decimals) : "0");
  useEffect(() => {
    const controls = animate(mv, Number(value) || 0, { duration: 0.9, ease: [0.16, 1, 0.3, 1] });
    const unsub = rounded.on("change", (v) => setText(v));
    return () => { controls.stop(); unsub(); };
  }, [value]);
  return <span>{text}</span>;
}

// ---- scoreboard ------------------------------------------------------------
export function Scoreboard({ children }) {
  return (
    <motion.div
      className="scoreboard"
      initial="hide"
      animate="show"
      variants={{ show: { transition: { staggerChildren: 0.06 } } }}
    >
      {children}
    </motion.div>
  );
}
export function Score({ label, value, sub, color, count, decimals }) {
  return (
    <motion.div
      className="score"
      style={{ "--tally": color }}
      variants={{ hide: { opacity: 0, y: 14 }, show: { opacity: 1, y: 0 } }}
    >
      <div className="score-label">{label}</div>
      <div className={"score-value" + (typeof value === "string" ? " sm" : "")}>
        {count ? <CountUp value={value} decimals={decimals} /> : value}
      </div>
      {sub && <div className="score-sub">{sub}</div>}
    </motion.div>
  );
}

// ---- routing chip ----------------------------------------------------------
const DECISION_LABEL = { auto: "Auto", review: "Review", escalate: "Escalate", ignore: "Ignore" };
export function DecisionChip({ decision }) {
  const d = decision || "ignore";
  return (
    <span className={`chip ${d}`}>
      <span className="dot" />
      {DECISION_LABEL[d] || d}
    </span>
  );
}

// ---- misc ------------------------------------------------------------------
export function Spinner({ dark }) {
  return <span className={"spinner" + (dark ? " dark" : "")} />;
}

export function Alert({ kind = "info", children }) {
  return <div className={`alert ${kind}`}>{children}</div>;
}

export function Toggle({ checked, onChange, label }) {
  return (
    <label className="toggle">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="track" />
      {label && <span>{label}</span>}
    </label>
  );
}

export function Field({ label, children }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
    </label>
  );
}

// wraps a page for enter animation
export function PageFade({ children }) {
  return (
    <motion.div
      className="page"
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.35 }}
    >
      {children}
    </motion.div>
  );
}
