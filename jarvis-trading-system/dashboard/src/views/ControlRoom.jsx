import { useEffect, useRef, useState } from "react";
import { MODE_LABEL, MODE_STYLE, MODES } from "../hooks/useAutonomy";

// ── helpers ───────────────────────────────────────────────────────────────────

function Section({ title, badge, children }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-4">
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
      <input
        type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-cyan-500 cursor-pointer"
      />
      <div className="flex justify-between text-[9px] text-gray-700">
        <span>{fmt ? fmt(min) : min}</span>
        <span>{fmt ? fmt(max) : max}</span>
      </div>
      {note && <p className="text-[9px] text-gray-700 mt-1">{note}</p>}
    </div>
  );
}

function TextField({ label, value, onChange, type = "text", placeholder }) {
  return (
    <div>
      <label className="text-xs text-gray-400 block mb-1">{label}</label>
      <input
        type={type}
        value={value ?? ""}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-xs text-gray-200
                   font-mono focus:outline-none focus:border-cyan-700 placeholder-gray-700"
      />
    </div>
  );
}

function SelectField({ label, value, onChange, options }) {
  return (
    <div>
      <label className="text-xs text-gray-400 block mb-1">{label}</label>
      <select
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-cyan-700"
      >
        {options.map((o) => (
          <option key={o.value ?? o} value={o.value ?? o}>{o.label ?? o}</option>
        ))}
      </select>
    </div>
  );
}

function Toggle({ label, checked, onChange, note }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div>
        <div className="text-xs text-gray-400">{label}</div>
        {note && <div className="text-[9px] text-gray-600 mt-0.5">{note}</div>}
      </div>
      <button
        onClick={() => onChange(!checked)}
        className={[
          "shrink-0 w-10 h-5 rounded-full border-2 transition-all relative",
          checked ? "bg-cyan-600 border-cyan-500" : "bg-gray-800 border-gray-700",
        ].join(" ")}
      >
        <span className={[
          "absolute top-0.5 w-3.5 h-3.5 rounded-full bg-white transition-all",
          checked ? "left-5" : "left-0.5",
        ].join(" ")} />
      </button>
    </div>
  );
}

const NOTIF_CATS = [
  { key: "REGIME",   label: "🔵 Regime changes" },
  { key: "ROTATION", label: "🟡 Strategy rotations" },
  { key: "ENTRY",    label: "🟢 Entry signals" },
  { key: "EXIT",     label: "✅ Exit / close" },
  { key: "SIZING",   label: "📐 Position sizing" },
  { key: "BRAIN",    label: "🧠 Brain retrains" },
  { key: "APPROVAL", label: "⚡ Approval requests" },
  { key: "RISK",     label: "🚨 Risk / kill switch" },
];

const IMMEDIATE = { label: "LIVE", cls: "text-green-400 border-green-800 bg-green-950/30" };
const RESTART   = { label: "RESTART", cls: "text-yellow-400 border-yellow-800 bg-yellow-950/30" };

const DEFAULT_S = {
  dhan_client_id: "", dhan_access_token: "", paper_mode: true,
  initial_capital: 10000, kill_switch_pct: 0.03, kelly_fraction: 0.5,
  hmm_states: 4, regime_lookback_bars: 200, sharpe_rank_window_days: 20,
  ws_port: 8765, log_level: "INFO",
  intent_log_path: "logs/intent.jsonl", pnl_db_path: "data/pnl.db",
};

export default function ControlRoom({
  mode, changeMode,
  approvalTimeout, setApprovalTimeout,
  filters, toggleFilter,
}) {
  const [s, setS]           = useState(DEFAULT_S);
  const [dirty, setDirty]   = useState(false);
  const [status, setStatus] = useState(null); // null | "saving" | "saved" | "restart" | "error"
  const origRef             = useRef(DEFAULT_S);

  // Load from server on mount
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

  const pct  = (v) => `${(v * 100).toFixed(1)}%`;
  const inr  = (v) => `₹${Number(v).toLocaleString("en-IN")}`;

  return (
    <div className="p-4 grid grid-cols-1 lg:grid-cols-2 gap-4 pb-8">

      {/* ── Autonomy ─────────────────────────────────────────────────── */}
      <Section title="Autonomy Mode" badge={IMMEDIATE} className="lg:col-span-2">
        <div className="flex gap-3">
          {MODES.map((m) => (
            <button
              key={m}
              onClick={() => changeMode(m)}
              className={[
                "flex-1 py-3 rounded border-2 font-bold tracking-[0.15em] text-sm transition-all",
                mode === m ? MODE_STYLE[m] : "border-gray-800 text-gray-700 hover:border-gray-700",
              ].join(" ")}
            >
              {MODE_LABEL[m]}
            </button>
          ))}
        </div>
        <div className="text-[9px] text-gray-700 space-y-0.5 border-t border-gray-800 pt-3">
          <div><span className="text-gray-500">MANUAL</span> — signals only, you place orders manually</div>
          <div><span className="text-yellow-700">SEMI AUTO</span> — JARVIS acts but shows each decision for approval</div>
          <div><span className="text-cyan-700">FULL AUTO</span> — fully autonomous within risk guardrails</div>
        </div>
        <SliderField
          label="Semi-auto approval timeout"
          min={10} max={120} step={5}
          value={approvalTimeout} onChange={setApprovalTimeout}
          fmt={(v) => `${v}s`}
        />
      </Section>

      {/* ── Broker credentials ───────────────────────────────────────── */}
      <Section title="Broker & Trading Mode" badge={RESTART}>
        <Toggle
          label="Paper trading mode"
          note="When ON, all orders are simulated. Switch OFF only when ready for live."
          checked={s.paper_mode}
          onChange={update("paper_mode")}
        />
        <TextField
          label="Dhan Client ID"
          value={s.dhan_client_id}
          onChange={update("dhan_client_id")}
          placeholder="Enter client ID…"
        />
        <TextField
          label="Dhan Access Token"
          value={s.dhan_access_token}
          onChange={update("dhan_access_token")}
          type="password"
          placeholder="Enter access token…"
        />
        <p className="text-[9px] text-gray-700">
          Credentials are stored in <code className="text-gray-500">data/settings.json</code> on the
          server and never sent to third parties. That file is excluded from git.
        </p>
      </Section>

      {/* ── Capital & Risk ───────────────────────────────────────────── */}
      <Section title="Capital & Risk" badge={IMMEDIATE}>
        <div>
          <label className="text-xs text-gray-400 block mb-1">Initial capital (₹)</label>
          <input
            type="number" min={1000} step={1000}
            value={s.initial_capital}
            onChange={(e) => update("initial_capital")(Number(e.target.value))}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-xs text-gray-200 font-mono focus:outline-none focus:border-cyan-700"
          />
          <p className="text-[9px] text-yellow-700 mt-1">Changing capital requires restart to take effect.</p>
        </div>
        <SliderField
          label="Kill switch drawdown %"
          min={0.01} max={0.10} step={0.005}
          value={s.kill_switch_pct} onChange={update("kill_switch_pct")}
          fmt={pct}
          note={`Hard stop triggers at ${inr(s.initial_capital * s.kill_switch_pct)} daily loss. Applied immediately.`}
        />
        <SliderField
          label="Kelly fraction"
          min={0.1} max={1.0} step={0.05}
          value={s.kelly_fraction} onChange={update("kelly_fraction")}
          fmt={(v) => `${v.toFixed(2)}×`}
          note="0.5 = Half-Kelly (recommended). Applied immediately."
        />
      </Section>

      {/* ── Intelligence ─────────────────────────────────────────────── */}
      <Section title="Intelligence & Regime" badge={RESTART}>
        <div>
          <label className="text-xs text-gray-400 block mb-1">HMM states</label>
          <input
            type="number" min={2} max={8} step={1}
            value={s.hmm_states}
            onChange={(e) => update("hmm_states")(Number(e.target.value))}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-xs text-gray-200 font-mono focus:outline-none focus:border-cyan-700"
          />
        </div>
        <SliderField
          label="Regime lookback bars"
          min={50} max={500} step={25}
          value={s.regime_lookback_bars} onChange={update("regime_lookback_bars")}
          fmt={(v) => `${v} bars`}
        />
        <SliderField
          label="Sharpe rank window (days)"
          min={5} max={60} step={5}
          value={s.sharpe_rank_window_days} onChange={update("sharpe_rank_window_days")}
          fmt={(v) => `${v}d`}
        />
      </Section>

      {/* ── Server & logging ─────────────────────────────────────────── */}
      <Section title="Server & Logging" badge={RESTART}>
        <div>
          <label className="text-xs text-gray-400 block mb-1">WebSocket port</label>
          <input
            type="number" min={1024} max={65535}
            value={s.ws_port}
            onChange={(e) => update("ws_port")(Number(e.target.value))}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-xs text-gray-200 font-mono focus:outline-none focus:border-cyan-700"
          />
        </div>
        <SelectField
          label="Log level"
          value={s.log_level}
          onChange={update("log_level")}
          options={["DEBUG", "INFO", "WARNING", "ERROR"]}
        />
        <TextField
          label="Intent log path"
          value={s.intent_log_path}
          onChange={update("intent_log_path")}
        />
        <TextField
          label="PnL database path"
          value={s.pnl_db_path}
          onChange={update("pnl_db_path")}
        />
      </Section>

      {/* ── Notifications ────────────────────────────────────────────── */}
      <Section title="Notifications" badge={IMMEDIATE}>
        <div className="space-y-2.5">
          {NOTIF_CATS.map(({ key, label }) => (
            <label key={key} className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={filters[key] ?? true}
                onChange={() => toggleFilter(key)}
                className="accent-cyan-500 w-3.5 h-3.5"
              />
              <span className="text-xs text-gray-400">{label}</span>
            </label>
          ))}
        </div>
        <button
          onClick={() => "Notification" in window && Notification.requestPermission()}
          className="text-[10px] text-cyan-700 hover:text-cyan-400 transition-colors"
        >
          Enable push notifications →
        </button>
      </Section>

      {/* ── Save bar ─────────────────────────────────────────────────── */}
      <div className="lg:col-span-2 bg-gray-900 border border-gray-800 rounded p-4 flex items-center gap-4">
        <button
          onClick={save}
          disabled={!dirty || status === "saving"}
          className={[
            "px-6 py-2 rounded font-bold tracking-widest text-sm transition-all border",
            dirty && status !== "saving"
              ? "border-cyan-600 bg-cyan-900/40 text-cyan-300 hover:bg-cyan-900/70"
              : "border-gray-800 text-gray-700 cursor-not-allowed",
          ].join(" ")}
        >
          {status === "saving" ? "SAVING…" : "SAVE TO JARVIS"}
        </button>

        {status === "saved" && (
          <span className="text-xs text-green-400 font-bold">✓ Saved — settings applied</span>
        )}
        {status === "restart" && (
          <span className="text-xs text-yellow-400 font-bold">
            ✓ Saved — some changes require restart (<code>uvicorn</code>)
          </span>
        )}
        {status === "error" && (
          <span className="text-xs text-red-400 font-bold">✗ Save failed — check server logs</span>
        )}
        {dirty && !status && (
          <span className="text-[10px] text-gray-600">Unsaved changes</span>
        )}

        <div className="ml-auto text-[9px] text-gray-700 text-right">
          <div><span className="text-green-700">LIVE</span> = applied to running engine immediately</div>
          <div><span className="text-yellow-700">RESTART</span> = saved to disk, effective on next server start</div>
        </div>
      </div>
    </div>
  );
}
