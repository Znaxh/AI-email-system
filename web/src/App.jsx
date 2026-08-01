import { createContext, useCallback, useContext, useEffect, useState } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { AnimatePresence } from "framer-motion";
import { api } from "./api";
import Assistant from "./pages/Assistant.jsx";
import Inbox from "./pages/Inbox.jsx";
import Review from "./pages/Review.jsx";
import Evaluation from "./pages/Evaluation.jsx";
import Settings from "./pages/Settings.jsx";

const NAV = [
  { to: "/", num: "01", label: "Assistant", el: Assistant },
  { to: "/inbox", num: "02", label: "Inbox", el: Inbox },
  { to: "/review", num: "03", label: "Review", el: Review, badgeKey: "pending_total" },
  { to: "/settings", num: "04", label: "Settings", el: Settings },
  { to: "/evaluation", num: "05", label: "Evaluation", el: Evaluation },
];

// Shared bootstrap (orders, config, queue counts). Refreshable from any page.
const AppCtx = createContext(null);
export const useApp = () => useContext(AppCtx);

function Ticker({ counts, config }) {
  const items = [
    ["Escalations", counts.escalate ?? 0],
    ["Needs review", counts.review ?? 0],
    ["Auto pending", counts.auto ?? 0],
    ["Total pending", counts.pending_total ?? 0],
  ];
  const send = config?.live_send ? "LIVE SEND" : "DRY-RUN";
  const line = (
    <span className="ticker-track" aria-hidden="true">
      {[0, 1].map((dup) =>
        items.map(([k, v], i) => (
          <span className="ticker-item" key={`${dup}-${i}`}>
            {k} <b>{v}</b>
            <span className="ticker-sep">/</span>
          </span>
        ))
      )}
      {[0, 1].map((dup) => (
        <span className="ticker-item" key={`s-${dup}`}>
          Source <b>{config?.email_source ?? "n/a"}</b>
          <span className="ticker-sep">/</span> Auto-send <b>{send}</b>
          <span className="ticker-sep">/</span> Thresholds <b>T1 {config?.t1} · T2 {config?.t2}</b>
          <span className="ticker-sep">//</span>
        </span>
      ))}
    </span>
  );
  return <div className="ticker">{line}</div>;
}

export default function App() {
  const location = useLocation();
  const [boot, setBoot] = useState({ orders: [], config: {}, categories: [], queue_counts: {} });

  const refresh = useCallback(async () => {
    try {
      setBoot(await api.get("/bootstrap"));
    } catch (e) {
      console.error("bootstrap failed", e);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  const counts = boot.queue_counts || {};

  return (
    <AppCtx.Provider value={{ ...boot, refresh }}>
      <div className="app">
        <aside className="rail">
          <div className="brand">
            <div className="brand-mark">Reply<span>.</span>Ops</div>
            <div className="brand-sub">Suggested-Response System</div>
          </div>
          <nav className="nav">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.to === "/"}
                className={({ isActive }) => "nav-item" + (isActive ? " active" : "")}
              >
                <span className="nav-num">{n.num}</span>
                <span className="nav-label">{n.label}</span>
                {n.badgeKey && counts[n.badgeKey] > 0 && (
                  <span className="nav-badge">{counts[n.badgeKey]}</span>
                )}
              </NavLink>
            ))}
          </nav>
          <div className="rail-foot">
            <span>
              <span className={"live-dot" + (boot.config?.live_send ? "" : " dry")} />
              {boot.config?.live_send ? "LIVE SEND" : "DRY-RUN"}
            </span>
            <span>SRC · {boot.config?.email_source ?? "n/a"}</span>
          </div>
        </aside>

        <div className="main">
          <Ticker counts={counts} config={boot.config} />
          <AnimatePresence mode="wait">
            <Routes location={location} key={location.pathname}>
              {NAV.map((n) => (
                <Route key={n.to} path={n.to} element={<n.el />} />
              ))}
            </Routes>
          </AnimatePresence>
        </div>
      </div>
    </AppCtx.Provider>
  );
}
