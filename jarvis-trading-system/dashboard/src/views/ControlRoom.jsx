import { useCallback, useEffect, useRef, useState } from "react";
import { MODE_LABEL, MODE_STYLE, MODES } from "../hooks/useAutonomy";

// ── Shared UI atoms ───────────────────────────────────────────────────────────

function Section({ title, badge, children, danger }) {
  return (
    <div className={[
      "border rounded p-4",
      danger ? "bg-red-950/20 border-red-900/60" : "bg-gray-900 border-gray-800",
    ].join(" ")}>
      <div className="flex items-center gap-2 mb-4">
        <span className="text-[10px] text-gray-500 tracking-widest uppercase">{title}</span>
        {badge && (
          <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded border tracking-widest ${badge.cls}`}>
            {badge.label}
          </span>
        )}
      </div>
      <div className="space-y-4">{children}</div>
    </div>
  );
}

function SliderField({ label, min, max, step, value, onChange, fmt, note }) {
  return (
    <div>
      <div className="flex justify-between mb-1">
        <span className="text-xs text-gray-400">{label}</span>
        <span className="text-xs font-mono text-gray-200 tabular-nums">{fmt ? fmt(value) : value}</span>
      </div>
      <input type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-cyan-500 cursor-pointer" />
      <div className="flex justify-between text-[9px] text-gray-700">
        <span>{fmt ? fmt(min) : min}</span><span>{fmt ? fmt(max) : max}</span>
      </div>
      {note && <p className="text-[9px] text-gray-600 mt-1">{note}</p>}
    </div>
  );
}

function TextField({ label, value, onChange, type = "text", placeholder }) {
  return (
    <div>
      <label className="text-xs text-gray-400 block mb-1">{label}</label>
      <input type={type} value={value ?? ""} placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-xs text-gray-200
                   font-mono focus:outline-none focus:border-cyan-700 placeholder-gray-700" />
    </div>
  );
}

function SelectField({ label, value, onChange, options }) {
  return (
    <div>
      <label className="text-xs text-gray-400 block mb-1">{label}</label>
      <select value={value ?? ""} onChange={(e) => onChange(e.target.value)}
        className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-cyan-700">
        {options.map((o) => (
          <option key={o.value ?? o} value={o.value ?? o}>{o.label ?? o}</option>
        ))}
      </select>
    </div>
  );
}

function Toggle({ label, checked, onChange, note, disabled }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <div className={`text-xs ${disabled ? "text-gray-600" : "text-gray-400"}`}>{label}</div>
        {note && <div className="text-[9px] text-gray-600 mt-0.5">{note}</div>}
      </div>
      <button onClick={() => !disabled && onChange(!checked)} disabled={disabled}
        className={[
          "shrink-0 w-10 h-5 rounded-full border-2 transition-all relative",
          disabled ? "opacity-40 cursor-not-allowed bg-gray-800 border-gray-700" :
            checked ? "bg-cyan-600 border-cyan-500 cursor-pointer" : "bg-gray-800 border-gray-700 cursor-pointer",
        ].join(" ")}>
        <span className={["absolute top-0.5 w-3.5 h-3.5 rounded-full bg-white transition-all",
          checked ? "left-5" : "left-0.5"].join(" ")} />
      </button>
    </div>
  );
}

const NOTIF_CATS = [
  { key: "REGIME",   label: "Regime changes" },
  { key: "ROTATION", label: "Strategy rotations" },
  { key: "ENTRY",    label: "Entry signals" },
  { key: "EXIT",     label: "Exit / close" },
  { key: "SIZING",   label: "Position sizing" },
  { key: "BRAIN",    label: "Brain retrains" },
  { key: "APPROVAL", label: "Approval requests" },
  { key: "RISK",     label: "Risk / kill switch" },
];

const IMMEDIATE = { label: "LIVE",    cls: "text-green-400 border-green-800 bg-green-950/30" };
const RESTART   = { label: "RESTART", cls: "text-yellow-400 border-yellow-800 bg-yellow-950/30" };

const ALL_STRATEGIES = [
  { id: "ema_crossover", label: "EMA Crossover" },
  { id: "supertrend",    label: "Supertrend" },
  { id: "orb_breakout",  label: "ORB Breakout" },
  { id: "rsi_momentum",  label: "RSI Momentum" },
  { id: "vwap_breakout", label: "VWAP Breakout" },
];

const DEFAULT_S = {
  dhan_client_id: "", dhan_access_token: "", paper_mode: true,
  initial_capital: 10000, kill_switch_pct: 0.03, kelly_fraction: 0.5,
  hmm_states: 4, regime_lookback_bars: 200, sharpe_rank_window_days: 20,
  ws_port: 8765, log_level: "INFO",
  intent_log_path: "logs/intent.jsonl", pnl_db_path: "data/pnl.db",
};

// ── Kill Switch panel ─────────────────────────────────────────────────────────

function KillSwitchPanel({ killed, onKill, onReset }) {
  const [confirming, setConfirming] = useState(false);

  const handleKill = () => {
    if (!confirming) { setConfirming(true); return; }
    setConfirming(false);
    onKill();
  };

  useEffect(() => {
    if (!confirming) return;
    const t = setTimeout(() => setConfirming(false), 4000);
    return () => clearTimeout(t);
  }, [confirming]);

  if (killed) {
    return (
      <div className="space-y-3">
        <div className="bg-red-900/30 border border-red-700 rounded p-3 text-center">
          <div className="text-red-400 font-bold tracking-widest text-sm">KILL SWITCH ACTIVE</div>
          <div className="text-[10px] text-red-600 mt-1">All trading halted — no new orders</div>
        </div>
        <button onClick={onReset}
          className="w-full py-2.5 rounded border-2 border-green-700 bg-green-950/30 text-green-400
                     font-bold tracking-widest text-sm hover:bg-green-900/40 transition-all">
          RESET KILL SWITCH
        </button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-[10px] text-gray-600">
        Immediately closes all open positions and halts all new orders.
        Tap once to arm, tap again to confirm.
      </p>
      <button onClick={handleKill}
        className={[
          "w-full py-3 rounded border-2 font-bold tracking-widest text-sm transition-all",
          confirming
            ? "border-red-500 bg-red-600 text-white animate-pulse"
            : "border-red-900 bg-red-950/40 text-red-500 hover:border-red-700 hover:text-red-400",
        ].join(" ")}>
        {confirming ? "TAP AGAIN TO CONFIRM STOP" : "EMERGENCY STOP"}
      </button>
    </div>
  );
}

// ── Active Watchlist ──────────────────────────────────────────────────────────

const STATUS_DOT = {
  live:      "bg-green-400 animate-pulse",
  stale:     "bg-yellow-500",
  searching: "bg-cyan-500 animate-pulse",
  offline:   "bg-gray-700",
};
const STATUS_TEXT = {
  live: "text-green-400", stale: "text-yellow-500",
  searching: "text-cyan-400", offline: "text-gray-600",
};
const BADGE_CLS = {
  EQ:  "text-blue-400 border-blue-800 bg-blue-950/30",
  FUT: "text-orange-400 border-orange-800 bg-orange-950/30",
  OPT: "text-purple-400 border-purple-800 bg-purple-950/30",
  IDX: "text-gray-400 border-gray-700 bg-gray-900/30",
};

function ActiveWatchlist({ snapshot, onSearchOpen }) {
  const [watchlist, setWatchlist] = useState([]);
  const intervalRef = useRef(null);

  const loadWatchlist = useCallback(() => {
    fetch("/api/instruments/watchlist")
      .then((r) => r.json())
      .then((d) => setWatchlist(d.instruments ?? []))
      .catch(() => {});
  }, []);

  useEffect(() => {
    loadWatchlist();
    intervalRef.current = setInterval(loadWatchlist, 30000);
    return () => clearInterval(intervalRef.current);
  }, [loadWatchlist]);

  // Merge live scanner data into watchlist entries
  const scanner = snapshot?.scanner ?? {};
  const enriched = watchlist.map((inst) => ({ ...inst, ...(scanner[inst.symbol] ?? {}) }));

  const unsubscribe = async (sym) => {
    await fetch("/api/instruments/unsubscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol: sym }),
    }).catch(() => {});
    setWatchlist((p) => p.filter((i) => i.symbol !== sym));
  };

  return (
    <div className="space-y-3">
      {/* Add button */}
      <button
        onClick={onSearchOpen}
        className="w-full flex items-center justify-center gap-2 py-2 rounded border border-dashed
                   border-cyan-800 text-cyan-700 text-xs font-mono hover:text-cyan-400
                   hover:border-cyan-600 transition-colors"
      >
        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <circle cx="11" cy="11" r="8" />
          <path d="M21 21l-4.35-4.35" strokeLinecap="round" />
        </svg>
        SEARCH &amp; ADD INSTRUMENTS
      </button>

      {/* Active instruments list */}
      {enriched.length === 0 ? (
        <p className="text-xs text-gray-600 text-center py-2">No instruments subscribed</p>
      ) : (
        <div className="space-y-1">
          <div className="text-[9px] text-gray-700 uppercase tracking-widest mb-1">Active instruments</div>
          {enriched.map((inst) => {
            const status = inst.status ?? "searching";
            const badge  = inst.badge ?? (inst.is_currency ? "FUT" : "EQ");
            const priceStr = inst.ltp != null
              ? (inst.is_currency ? inst.ltp.toFixed(4) : `₹${inst.ltp.toFixed(2)}`)
              : "—";
            return (
              <div key={inst.symbol}
                className="flex items-center gap-2 bg-gray-800/50 border border-gray-800 rounded px-2 py-1.5">
                <span className={`w-2 h-2 rounded-full shrink-0 ${STATUS_DOT[status] ?? STATUS_DOT.searching}`} />
                <span className={`text-[8px] font-bold px-1 py-0.5 rounded border tracking-widest shrink-0 ${BADGE_CLS[badge] ?? BADGE_CLS.EQ}`}>
                  {badge}
                </span>
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-mono text-gray-200 truncate">
                    {inst.display || inst.symbol}
                  </div>
                  {inst.seg_label && (
                    <div className="text-[9px] text-gray-700">{inst.seg_label}</div>
                  )}
                </div>
                <span className={`text-xs font-mono tabular-nums ${STATUS_TEXT[status] ?? STATUS_TEXT.searching}`}>
                  {priceStr}
                </span>
                <button onClick={() => unsubscribe(inst.symbol)}
                  className="shrink-0 w-5 h-5 flex items-center justify-center text-gray-700
                             hover:text-red-400 transition-colors font-bold text-base leading-none">
                  ×
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Strategy toggle panel ─────────────────────────────────────────────────────

function StrategyPanel() {
  const [states, setStates] = useState({});

  useEffect(() => {
    fetch("/api/strategies/state")
      .then((r) => r.json())
      .then(setStates)
      .catch(() => {});
  }, []);

  const toggle = async (id, enabled) => {
    setStates((p) => ({ ...p, [id]: enabled }));
    await fetch("/api/strategy/toggle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id, enabled }),
    }).catch(() => {});
  };

  return (
    <div className="space-y-3">
      {ALL_STRATEGIES.map(({ id, label }) => (
        <Toggle
          key={id}
          label={label}
          checked={states[id] !== false}
          onChange={(val) => toggle(id, val)}
        />
      ))}
      <p className="text-[9px] text-gray-700">Changes take effect immediately — no restart needed.</p>
    </div>
  );
}

// ── Positions panel ───────────────────────────────────────────────────────────

function PositionsPanel({ snapshot }) {
  const [closing, setClosing] = useState({});

  const positions = Object.entries(snapshot?.broker?.open_positions ?? {});

  const closeOne = async (symbol) => {
    setClosing((p) => ({ ...p, [symbol]: true }));
    await fetch("/api/position/close", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ symbol }),
    }).catch(() => {});
    setTimeout(() => setClosing((p) => ({ ...p, [symbol]: false })), 2000);
  };

  const closeAll = async () => {
    setClosing({ _all: true });
    await fetch("/api/position/close", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ all: true }),
    }).catch(() => {});
    setTimeout(() => setClosing({}), 2000);
  };

  if (!positions.length) {
    return <p className="text-xs text-gray-600 text-center py-2">No open positions</p>;
  }

  return (
    <div className="space-y-2">
      {positions.map(([sym, pos]) => {
        const pnl = pos.unrealized_pnl ?? 0;
        const side = pos.qty > 0 ? "BUY" : "SELL";
        return (
          <div key={sym} className="flex items-center gap-3 bg-gray-800/60 border border-gray-700 rounded px-3 py-2">
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-xs font-mono text-gray-200 font-bold">{sym}</span>
                <span className={`text-[9px] font-bold px-1 rounded ${side === "BUY" ? "text-green-400 bg-green-950/40" : "text-red-400 bg-red-950/40"}`}>
                  {side}
                </span>
              </div>
              <div className="text-[10px] text-gray-500 mt-0.5">
                {Math.abs(pos.qty)} @ ₹{pos.avg_price?.toFixed(2)}
                <span className={`ml-2 font-mono ${pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                  {pnl >= 0 ? "+" : ""}₹{pnl.toFixed(2)}
                </span>
              </div>
            </div>
            <button
              onClick={() => closeOne(sym)}
              disabled={closing[sym] || closing._all}
              className="shrink-0 px-2.5 py-1 rounded border border-red-900 text-red-500 text-[10px]
                         font-bold hover:border-red-600 hover:text-red-400 transition-all disabled:opacity-40">
              {closing[sym] ? "…" : "CLOSE"}
            </button>
          </div>
        );
      })}
      {positions.length > 1 && (
        <button onClick={closeAll} disabled={!!closing._all}
          className="w-full py-1.5 rounded border border-red-900/60 text-red-600 text-xs
                     font-bold hover:border-red-700 hover:text-red-500 transition-all disabled:opacity-40">
          {closing._all ? "CLOSING ALL…" : "CLOSE ALL POSITIONS"}
        </button>
      )}
    </div>
  );
}

// ── Main ControlRoom ──────────────────────────────────────────────────────────

export default function ControlRoom({
  snapshot,
  mode, changeMode,
  approvalTimeout, setApprovalTimeout,
  filters, toggleFilter,
  onKill,
  onSearchOpen,
}) {
  const [s, setS]           = useState(DEFAULT_S);
  const [dirty, setDirty]   = useState(false);
  const [status, setStatus] = useState(null);
  const origRef             = useRef(DEFAULT_S);

  const killed = snapshot?.broker?.kill_switch_active ?? false;

  useEffect(() => {
    fetch("/api/settings")
      .then((r) => r.json())
      .then((data) => {
        const merged = { ...DEFAULT_S, ...data };
        setS(merged);
        origRef.current = merged;
      })
      .catch(() => {});
  }, []);

  const update = (key) => (val) => {
    setS((prev) => ({ ...prev, [key]: val }));
    setDirty(true);
    setStatus(null);
  };

  const save = async () => {
    setStatus("saving");
    try {
      const res = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(s),
      });
      const data = await res.json();
      setStatus(data.restart_required ? "restart" : "saved");
      setDirty(false);
      origRef.current = s;
    } catch {
      setStatus("error");
    }
  };

  const handleReset = async () => {
    await fetch("/api/kill/reset", { method: "POST" }).catch(() => {});
  };

  const pct = (v) => `${(v * 100).toFixed(1)}%`;
  const inr = (v) => `₹${Number(v).toLocaleString("en-IN")}`;

  return (
    <div className="p-4 grid grid-cols-1 lg:grid-cols-2 gap-4 pb-8">

      {/* ── Kill Switch ─────────────────────────────────────────── */}
      <Section title="Emergency Stop" danger>
        <KillSwitchPanel killed={killed} onKill={onKill} onReset={handleReset} />
      </Section>

      {/* ── Open Positions ───────────────────────────────────────── */}
      <Section title="Open Positions" badge={IMMEDIATE}>
        <PositionsPanel snapshot={snapshot} />
      </Section>

      {/* ── Autonomy ─────────────────────────────────────────────── */}
      <Section title="Autonomy Mode" badge={IMMEDIATE} className="lg:col-span-2">
        <div className="flex gap-3">
          {MODES.map((m) => (
            <button key={m} onClick={() => changeMode(m)}
              className={[
                "flex-1 py-3 rounded border-2 font-bold tracking-[0.15em] text-sm transition-all",
                mode === m ? MODE_STYLE[m] : "border-gray-800 text-gray-700 hover:border-gray-700",
              ].join(" ")}>
              {MODE_LABEL[m]}
            </button>
          ))}
        </div>
        <div className="text-[9px] text-gray-700 space-y-0.5 border-t border-gray-800 pt-3">
          <div><span className="text-gray-500">MANUAL</span> — signals only, you place orders manually</div>
          <div><span className="text-yellow-700">SEMI AUTO</span> — JARVIS acts but shows each decision for approval</div>
          <div><span className="text-cyan-700">FULL AUTO</span> — fully autonomous within risk guardrails</div>
        </div>
        <SliderField label="Semi-auto approval timeout"
          min={10} max={120} step={5}
          value={approvalTimeout} onChange={setApprovalTimeout}
          fmt={(v) => `${v}s`} />
      </Section>

      {/* ── Active Instruments ──────────────────────────────────── */}
      <Section title="Active Instruments" badge={IMMEDIATE}>
        <ActiveWatchlist snapshot={snapshot} onSearchOpen={onSearchOpen} />
      </Section>

      {/* ── Strategies ───────────────────────────────────────────── */}
      <Section title="Strategies" badge={IMMEDIATE}>
        <StrategyPanel />
      </Section>

      {/* ── Broker credentials ───────────────────────────────────── */}
      <Section title="Broker & Trading Mode" badge={RESTART}>
        <Toggle label="Paper trading mode"
          note="When ON, all orders are simulated. Switch OFF only when ready for live."
          checked={s.paper_mode} onChange={update("paper_mode")} />
        <TextField label="Dhan Client ID" value={s.dhan_client_id}
          onChange={update("dhan_client_id")} placeholder="Enter client ID…" />
        <TextField label="Dhan Access Token" value={s.dhan_access_token}
          onChange={update("dhan_access_token")} type="password"
          placeholder="Enter access token…" />
        <p className="text-[9px] text-gray-700">
          Stored in <code className="text-gray-500">data/settings.json</code> — excluded from git.
        </p>
      </Section>

      {/* ── Capital & Risk ───────────────────────────────────────── */}
      <Section title="Capital & Risk" badge={IMMEDIATE}>
        <div>
          <label className="text-xs text-gray-400 block mb-1">Initial capital (₹)</label>
          <input type="number" min={1000} step={1000} value={s.initial_capital}
            onChange={(e) => update("initial_capital")(Number(e.target.value))}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-xs text-gray-200 font-mono focus:outline-none focus:border-cyan-700" />
          <p className="text-[9px] text-yellow-700 mt-1">Changing capital requires restart.</p>
        </div>
        <SliderField label="Kill switch drawdown %"
          min={0.01} max={0.10} step={0.005}
          value={s.kill_switch_pct} onChange={update("kill_switch_pct")} fmt={pct}
          note={`Hard stop at ${inr(s.initial_capital * s.kill_switch_pct)} daily loss.`} />
        <SliderField label="Kelly fraction"
          min={0.1} max={1.0} step={0.05}
          value={s.kelly_fraction} onChange={update("kelly_fraction")}
          fmt={(v) => `${v.toFixed(2)}×`}
          note="0.5 = Half-Kelly (recommended). Applied immediately." />
      </Section>

      {/* ── Intelligence ─────────────────────────────────────────── */}
      <Section title="Intelligence & Regime" badge={RESTART}>
        <div>
          <label className="text-xs text-gray-400 block mb-1">HMM states</label>
          <input type="number" min={2} max={8} step={1} value={s.hmm_states}
            onChange={(e) => update("hmm_states")(Number(e.target.value))}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-xs text-gray-200 font-mono focus:outline-none focus:border-cyan-700" />
        </div>
        <SliderField label="Regime lookback bars"
          min={50} max={500} step={25}
          value={s.regime_lookback_bars} onChange={update("regime_lookback_bars")}
          fmt={(v) => `${v} bars`} />
        <SliderField label="Sharpe rank window (days)"
          min={5} max={60} step={5}
          value={s.sharpe_rank_window_days} onChange={update("sharpe_rank_window_days")}
          fmt={(v) => `${v}d`} />
      </Section>

      {/* ── Server & logging ─────────────────────────────────────── */}
      <Section title="Server & Logging" badge={RESTART}>
        <SelectField label="Log level" value={s.log_level}
          onChange={update("log_level")}
          options={["DEBUG", "INFO", "WARNING", "ERROR"]} />
        <TextField label="Intent log path" value={s.intent_log_path}
          onChange={update("intent_log_path")} />
        <TextField label="PnL database path" value={s.pnl_db_path}
          onChange={update("pnl_db_path")} />
      </Section>

      {/* ── Notifications ────────────────────────────────────────── */}
      <Section title="Notifications" badge={IMMEDIATE}>
        <div className="space-y-2.5">
          {NOTIF_CATS.map(({ key, label }) => (
            <label key={key} className="flex items-center gap-2 cursor-pointer">
              <input type="checkbox" checked={filters[key] ?? true}
                onChange={() => toggleFilter(key)} className="accent-cyan-500 w-3.5 h-3.5" />
              <span className="text-xs text-gray-400">{label}</span>
            </label>
          ))}
        </div>
        <button onClick={() => "Notification" in window && Notification.requestPermission()}
          className="text-[10px] text-cyan-700 hover:text-cyan-400 transition-colors">
          Enable push notifications →
        </button>
      </Section>

      {/* ── Save bar ─────────────────────────────────────────────── */}
      <div className="lg:col-span-2 bg-gray-900 border border-gray-800 rounded p-4 flex items-center gap-4">
        <button onClick={save} disabled={!dirty || status === "saving"}
          className={[
            "px-6 py-2 rounded font-bold tracking-widest text-sm transition-all border",
            dirty && status !== "saving"
              ? "border-cyan-600 bg-cyan-900/40 text-cyan-300 hover:bg-cyan-900/70"
              : "border-gray-800 text-gray-700 cursor-not-allowed",
          ].join(" ")}>
          {status === "saving" ? "SAVING…" : "SAVE SETTINGS"}
        </button>

        {status === "saved"   && <span className="text-xs text-green-400 font-bold">✓ Saved</span>}
        {status === "restart" && <span className="text-xs text-yellow-400 font-bold">✓ Saved — restart required</span>}
        {status === "error"   && <span className="text-xs text-red-400 font-bold">✗ Save failed</span>}
        {dirty && !status     && <span className="text-[10px] text-gray-600">Unsaved changes</span>}

        <div className="ml-auto text-[9px] text-gray-700 text-right hidden sm:block">
          <div><span className="text-green-700">LIVE</span> = applied immediately</div>
          <div><span className="text-yellow-700">RESTART</span> = effective on next start</div>
        </div>
      </div>
    </div>
  );
}
