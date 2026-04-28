import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatCurrency, pnlClass } from "../lib/utils";

// ── Constants ─────────────────────────────────────────────────────────────────

const REGIMES = ["TRENDING_UP", "TRENDING_DOWN", "SIDEWAYS", "HIGH_VOL"];
const REGIME_COLOUR = {
  TRENDING_UP:   "text-green-400",
  TRENDING_DOWN: "text-red-400",
  SIDEWAYS:      "text-yellow-400",
  HIGH_VOL:      "text-orange-400",
};

// ── Shared helpers ────────────────────────────────────────────────────────────

function fmtTs(ts) {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleTimeString("en-IN", {
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
}

function fmtPct(v) {
  return (v * 100).toFixed(2) + "%";
}

function fmtMs(ms) {
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
}

// ── Source badge ──────────────────────────────────────────────────────────────

function SourceBadge({ source }) {
  const styles = {
    ai:    "bg-cyan-900/40 text-cyan-400 border-cyan-800",
    cache: "bg-purple-900/40 text-purple-400 border-purple-800",
    rules: "bg-gray-800 text-gray-500 border-gray-700",
  };
  return (
    <span className={`text-[8px] font-bold px-1.5 py-0.5 rounded border tracking-wide ${styles[source] ?? styles.rules}`}>
      {(source ?? "rules").toUpperCase()}
    </span>
  );
}

// ── Direction badge ───────────────────────────────────────────────────────────

function DirBadge({ direction }) {
  if (direction === "long")  return <span className="text-green-400 font-bold text-[11px]">▲ LONG</span>;
  if (direction === "short") return <span className="text-red-400   font-bold text-[11px]">▼ SHORT</span>;
  return <span className="text-gray-600 text-[11px]">— FLAT</span>;
}

// ── Conviction bar ────────────────────────────────────────────────────────────

function ConvictionBar({ conviction }) {
  const pct  = Math.min(100, Math.max(0, conviction));
  const fill = pct >= 80 ? "#4ade80" : pct >= 72 ? "#facc15" : "#f87171";
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-16 h-1.5 bg-gray-800 rounded-full overflow-hidden">
        <div style={{ width: `${pct}%`, backgroundColor: fill }} className="h-full rounded-full transition-all" />
      </div>
      <span className="text-[10px] font-mono tabular-nums" style={{ color: fill }}>{pct}</span>
    </div>
  );
}

// ── Budget progress bar ───────────────────────────────────────────────────────

function BudgetBar({ pct }) {
  const pctN  = Math.min(1, Math.max(0, pct ?? 0));
  const pctD  = Math.round(pctN * 100);
  const fill  = pctN < 0.6 ? "#4ade80" : pctN < 0.85 ? "#facc15" : "#f87171";
  const label = pctN >= 0.85 ? "HIGH" : pctN >= 0.60 ? "MED" : "OK";
  return (
    <div className="flex items-center gap-2 flex-1">
      <div className="flex-1 h-1.5 bg-gray-800 rounded-full overflow-hidden">
        <div
          style={{ width: `${pctD}%`, backgroundColor: fill }}
          className="h-full rounded-full transition-all duration-700"
        />
      </div>
      <span className="text-[9px] font-mono tabular-nums w-8 text-right" style={{ color: fill }}>
        {pctD}%
      </span>
      <span className="text-[8px] font-bold tracking-wide" style={{ color: fill }}>{label}</span>
    </div>
  );
}

// ── Mode badge ────────────────────────────────────────────────────────────────

function ModeBadge({ mode }) {
  const styles = {
    normal:     "bg-green-900/30 text-green-400 border-green-800",
    restricted: "bg-yellow-900/30 text-yellow-400 border-yellow-800",
    rules_only: "bg-orange-900/30 text-orange-400 border-orange-800",
    paused:     "bg-gray-800 text-gray-500 border-gray-700",
  };
  const key = (mode ?? "normal").toLowerCase().replace(/[\s-]/g, "_");
  return (
    <span className={`text-[8px] font-bold px-1.5 py-0.5 rounded border tracking-wide ${styles[key] ?? styles.normal}`}>
      {(mode ?? "NORMAL").toUpperCase().replace("_", " ")}
    </span>
  );
}

// ── AI Brain Status card ───────────────────────────────────────────────────────

function AiBrainStatus({ brain, onToggle, toggling }) {
  const enabled = brain?.enabled ?? false;
  const budgetPct = brain?.budget_pct_used ?? 0;
  const costInr   = brain?.daily_cost_inr ?? 0;
  const costUsd   = brain?.daily_cost_usd ?? 0;

  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-4">
      {/* Header row */}
      <div className="flex items-center justify-between mb-3">
        <span className="text-[10px] text-gray-500 tracking-widest uppercase">AI Brain — Layer 5</span>
        <div className="flex items-center gap-2">
          <ModeBadge mode={brain?.mode} />
          <span className={`text-[8px] font-bold px-1.5 py-0.5 rounded border tracking-wide ${
            enabled
              ? "bg-green-900/30 text-green-400 border-green-800"
              : "bg-gray-800 text-gray-500 border-gray-700"
          }`}>
            {enabled ? "ONLINE" : "OFFLINE"}
          </span>
          <button
            disabled={toggling}
            onClick={() => onToggle(!enabled)}
            className={`text-[9px] px-2 py-1 rounded border transition-colors ${
              enabled
                ? "border-red-800 text-red-400 hover:bg-red-900/20"
                : "border-green-800 text-green-400 hover:bg-green-900/20"
            } disabled:opacity-40`}
          >
            {toggling ? "…" : enabled ? "Disable" : "Enable"}
          </button>
        </div>
      </div>

      {/* Metrics row */}
      <div className="grid grid-cols-3 gap-4 mb-3">
        <div>
          <div className="text-[9px] text-gray-700 tracking-widest uppercase mb-1">Daily Cost</div>
          <div className="text-[13px] font-mono tabular-nums text-gray-300">
            ₹{costInr.toFixed(2)}
          </div>
          <div className="text-[9px] text-gray-600 font-mono">${costUsd.toFixed(4)}</div>
        </div>
        <div className="col-span-2">
          <div className="text-[9px] text-gray-700 tracking-widest uppercase mb-1">Budget Used</div>
          <BudgetBar pct={budgetPct} />
          <div className="text-[9px] text-gray-700 mt-1">₹{(budgetPct * (brain?.budget?.daily_inr ?? 1680)).toFixed(0)} of ₹{(brain?.budget?.daily_inr ?? 1680).toFixed(0)}/day</div>
        </div>
      </div>
    </div>
  );
}

// ── Decision row (expandable) ─────────────────────────────────────────────────

function DecisionRow({ d, expanded, onToggle }) {
  return (
    <>
      <tr
        className="border-b border-gray-800/50 hover:bg-gray-800/30 cursor-pointer select-none"
        onClick={onToggle}
      >
        <td className="py-2 pr-2 text-[10px] font-mono text-gray-300">{d.symbol}</td>
        <td className="py-2 pr-3"><DirBadge direction={d.direction} /></td>
        <td className="py-2 pr-3"><ConvictionBar conviction={d.conviction} /></td>
        <td className="py-2 pr-2 text-[10px] font-mono tabular-nums text-gray-400">
          {fmtPct(d.stop_loss_pct)}
        </td>
        <td className="py-2 pr-2 text-[10px] font-mono tabular-nums text-gray-400">
          {fmtPct(d.take_profit_pct)}
        </td>
        <td className="py-2 pr-2"><SourceBadge source={d.source} /></td>
        <td className="py-2 pr-2 text-[9px] text-gray-600 font-mono truncate max-w-[90px]">
          {d.model_used?.replace("claude-", "").replace("gemini-", "g-").replace("gpt-", "") ?? "—"}
        </td>
        <td className="py-2 pr-2 text-[10px] font-mono tabular-nums text-gray-600">
          {fmtMs(d.latency_ms)}
        </td>
        <td className="py-2 text-[9px] font-mono tabular-nums text-gray-700">
          ${(d.cost_usd ?? 0).toFixed(5)}
        </td>
        <td className="py-2 pl-2 text-[9px] text-gray-700 tabular-nums">
          {fmtTs(d.ts)}
        </td>
      </tr>
      {expanded && (
        <tr className="bg-gray-900/60">
          <td colSpan={10} className="px-3 py-2 text-[10px]">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              <div>
                <span className="text-gray-600 tracking-widest uppercase text-[9px]">Reasoning</span>
                <p className="text-gray-400 mt-0.5 leading-relaxed">{d.reasoning || "—"}</p>
              </div>
              <div>
                <span className="text-gray-600 tracking-widest uppercase text-[9px]">Risk Notes</span>
                <p className="text-gray-500 mt-0.5 leading-relaxed">{d.risk_notes || "—"}</p>
              </div>
            </div>
            <div className="flex gap-4 mt-2 text-[9px] text-gray-700">
              <span>Size: {fmtPct(d.size_pct)}</span>
              <span>Consensus: {d.consensus ? "yes" : "no"}</span>
              <span>Actionable: {d.is_actionable ? <span className="text-green-500">yes</span> : <span className="text-gray-600">no</span>}</span>
              {d.shortlist_rank && <span>Rank: #{d.shortlist_rank}</span>}
              {d.final_score != null && <span>Score: {(d.final_score * 100).toFixed(1)}</span>}
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

// ── Recent Decisions panel ────────────────────────────────────────────────────

function RecentDecisions({ decisions }) {
  const [expanded, setExpanded] = useState(null);
  const rows = decisions ?? [];

  const toggle = (i) => setExpanded((prev) => (prev === i ? null : i));

  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-[10px] text-gray-500 tracking-widest uppercase">Recent AI Decisions</span>
        <span className="text-[9px] text-gray-700">{rows.length} shown</span>
      </div>

      {rows.length === 0 ? (
        <div className="text-center text-gray-700 text-xs py-6">
          — no AI decisions yet — brain cycles every 5 min —
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px]">
            <thead>
              <tr className="border-b border-gray-800">
                {["Symbol", "Dir", "Conviction", "SL%", "TP%", "Source", "Model", "Latency", "Cost", "Time"].map((h) => (
                  <th key={h} className="pb-1.5 text-left text-[9px] text-gray-700 tracking-widest uppercase font-normal pr-2">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((d, i) => (
                <DecisionRow
                  key={`${d.ts}-${d.symbol}-${i}`}
                  d={d}
                  expanded={expanded === i}
                  onToggle={() => toggle(i)}
                />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Monitored Positions panel ─────────────────────────────────────────────────

function MonitoredPositions({ positions }) {
  const entries = Object.entries(positions ?? {});

  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-[10px] text-gray-500 tracking-widest uppercase">AI-Monitored Positions</span>
        <span className="text-[9px] text-gray-700">{entries.length} active</span>
      </div>

      {entries.length === 0 ? (
        <div className="text-center text-gray-700 text-xs py-6">
          — no AI positions open —
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px]">
            <thead>
              <tr className="border-b border-gray-800">
                {["Symbol", "Dir", "Entry", "Current", "P&L%", "SL", "TP", "Trail", "Size%", "Held"].map((h) => (
                  <th key={h} className="pb-1.5 text-left text-[9px] text-gray-700 tracking-widest uppercase font-normal pr-3">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {entries.map(([sym, pos]) => {
                const pnl     = pos.unrealised_pnl_pct ?? 0;
                const heldMin = ((pos.held_seconds ?? 0) / 60).toFixed(0);
                return (
                  <tr key={sym} className="border-b border-gray-800/40 hover:bg-gray-800/20">
                    <td className="py-1.5 pr-3 text-[10px] font-mono text-gray-300">{sym}</td>
                    <td className="py-1.5 pr-3"><DirBadge direction={pos.direction} /></td>
                    <td className="py-1.5 pr-3 text-[10px] font-mono tabular-nums text-gray-400">
                      {(pos.entry_price ?? 0).toFixed(4)}
                    </td>
                    <td className="py-1.5 pr-3 text-[10px] font-mono tabular-nums text-gray-300">
                      {(pos.current_price ?? 0).toFixed(4)}
                    </td>
                    <td className={`py-1.5 pr-3 text-[10px] font-mono tabular-nums font-bold ${pnl >= 0 ? "text-green-400" : "text-red-400"}`}>
                      {pnl >= 0 ? "+" : ""}{pnl.toFixed(3)}%
                    </td>
                    <td className="py-1.5 pr-3 text-[10px] font-mono tabular-nums text-red-400/70">
                      {(pos.stop_loss_price ?? 0).toFixed(4)}
                    </td>
                    <td className="py-1.5 pr-3 text-[10px] font-mono tabular-nums text-green-400/70">
                      {(pos.take_profit_price ?? 0).toFixed(4)}
                    </td>
                    <td className="py-1.5 pr-3">
                      {pos.trail_activated ? (
                        <span className="text-[8px] font-bold text-purple-400 border border-purple-800 bg-purple-900/30 px-1.5 py-0.5 rounded tracking-wide">
                          TRAIL
                        </span>
                      ) : (
                        <span className="text-gray-700 text-[9px]">—</span>
                      )}
                    </td>
                    <td className="py-1.5 pr-3 text-[10px] font-mono tabular-nums text-gray-600">
                      {((pos.size_pct ?? 0) * 100).toFixed(1)}%
                    </td>
                    <td className="py-1.5 text-[9px] text-gray-700 font-mono">
                      {heldMin}m
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

// ── Legacy sub-panels (kept from original) ────────────────────────────────────

function BrainVersionList({ versions }) {
  if (!versions?.length) {
    return <div className="text-[11px] text-gray-700 py-4 text-center">— no versions saved yet —</div>;
  }
  return (
    <div className="space-y-1">
      {versions.slice().reverse().map((v, i) => (
        <div key={v.name} className="flex items-center gap-3 py-1.5 border-b border-gray-800/40">
          <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${i === 0 ? "bg-cyan-900/40 text-cyan-400 border border-cyan-800" : "text-gray-600"}`}>
            {i === 0 ? "CURRENT" : v.name.slice(0, 14)}
          </span>
          <span className="text-[10px] font-mono text-gray-500 flex-1">{v.name}</span>
          {v.sharpe != null && (
            <span className={`text-[10px] font-mono tabular-nums ${v.sharpe > 0 ? "text-green-400" : "text-red-400"}`}>
              SR {v.sharpe.toFixed(2)}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

function ConfusionMatrix({ matrix }) {
  return (
    <div className="overflow-x-auto">
      <table className="text-[10px] border-collapse mx-auto">
        <thead>
          <tr>
            <th className="p-1 text-gray-700 text-[9px]">Pred →</th>
            {REGIMES.map((r) => (
              <th key={r} className="p-1 text-gray-600 text-[9px] w-16 text-center">
                {r.replace("_", " ").slice(0, 6)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {REGIMES.map((actual) => (
            <tr key={actual}>
              <td className="pr-2 text-gray-600 text-[9px] whitespace-nowrap">
                {actual.replace("_", " ").slice(0, 6)}
              </td>
              {REGIMES.map((pred) => {
                const val = matrix?.[actual]?.[pred] ?? 0;
                const intensity = Math.min(val / 20, 1);
                return (
                  <td
                    key={pred}
                    className="p-1 text-center font-mono border border-gray-800"
                    style={{
                      backgroundColor: `rgba(34,211,238,${intensity * 0.5})`,
                      color: intensity > 0.3 ? "#e2e8f0" : "#4b5563",
                    }}
                  >
                    {val}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Main view ─────────────────────────────────────────────────────────────────

export default function BrainAnalytics({ snapshot, pnlHistory }) {
  const [equity, setEquity]   = useState([]);
  const [intents, setIntents] = useState([]);
  const [matrix]              = useState(null);
  const [toggling, setToggling] = useState(false);

  useEffect(() => {
    fetch("/api/equity").then((r) => r.json()).then((d) => setEquity(d.curve ?? [])).catch(() => {});
    fetch("/api/intent").then((r) => r.json()).then((d) => setIntents(d.events ?? [])).catch(() => {});
  }, []);

  const brain = snapshot?.ai_brain ?? {};
  const brainEvents = intents.filter((e) =>
    ["BRAIN_RETRAIN", "REGIME_CHANGE", "STRATEGY_ROTATION"].includes(e.event_type)
  );
  const equityData = equity.map((e) => ({ date: e.date, equity: e.equity }));

  const handleToggle = async (enable) => {
    setToggling(true);
    try {
      await fetch("/api/ai/brain/toggle", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ enabled: enable }),
      });
    } finally {
      setToggling(false);
    }
  };

  return (
    <div className="p-4 grid grid-cols-1 lg:grid-cols-2 gap-4">

      {/* ── Equity curve ─────────────────────────────────────────────────── */}
      <div className="bg-gray-900 border border-gray-800 rounded p-4 lg:col-span-2">
        <div className="text-[10px] text-gray-500 tracking-widest uppercase mb-3">Equity Curve</div>
        {equityData.length > 1 ? (
          <ResponsiveContainer width="100%" height={160}>
            <LineChart data={equityData} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
              <XAxis dataKey="date" tick={{ fill: "#374151", fontSize: 9 }} tickLine={false} axisLine={false} />
              <YAxis tick={{ fill: "#374151", fontSize: 9 }} width={56}
                tickFormatter={(v) => `₹${(v / 1000).toFixed(0)}k`} tickLine={false} axisLine={false} />
              <Tooltip
                contentStyle={{ background: "#111827", border: "1px solid #374151", fontSize: 11 }}
                formatter={(v) => [formatCurrency(v), "Equity"]}
              />
              <Line type="monotone" dataKey="equity" stroke="#22d3ee" strokeWidth={2}
                dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="text-center text-gray-700 text-xs py-12">
            — equity curve builds after first trading day —
          </div>
        )}
      </div>

      {/* ── AI Brain status ──────────────────────────────────────────────── */}
      <div className="lg:col-span-2">
        <AiBrainStatus brain={brain} onToggle={handleToggle} toggling={toggling} />
      </div>

      {/* ── Recent AI decisions ───────────────────────────────────────────── */}
      <div className="lg:col-span-2">
        <RecentDecisions decisions={brain.decisions} />
      </div>

      {/* ── Monitored positions ───────────────────────────────────────────── */}
      <div className="lg:col-span-2">
        <MonitoredPositions positions={brain.monitored_positions} />
      </div>

      {/* ── Brain versions ────────────────────────────────────────────────── */}
      <div className="bg-gray-900 border border-gray-800 rounded p-4">
        <div className="text-[10px] text-gray-500 tracking-widest uppercase mb-3">Brain Versions</div>
        <BrainVersionList versions={snapshot?.brain_versions ?? []} />
      </div>

      {/* ── Regime confusion matrix ───────────────────────────────────────── */}
      <div className="bg-gray-900 border border-gray-800 rounded p-4">
        <div className="text-[10px] text-gray-500 tracking-widest uppercase mb-3">
          Regime Confusion Matrix
        </div>
        <ConfusionMatrix matrix={matrix} />
        {!matrix && (
          <div className="text-center text-gray-700 text-[10px] mt-2">
            populated after HMM re-labels
          </div>
        )}
      </div>

      {/* ── Brain event log ───────────────────────────────────────────────── */}
      <div className="bg-gray-900 border border-gray-800 rounded p-4 lg:col-span-2">
        <div className="text-[10px] text-gray-500 tracking-widest uppercase mb-3">Brain Event Log</div>
        {brainEvents.length === 0 ? (
          <div className="text-center text-gray-700 text-xs py-4">— no brain events yet —</div>
        ) : (
          <div className="space-y-1 max-h-48 overflow-y-auto font-mono text-[10px]">
            {brainEvents.slice().reverse().map((e, i) => (
              <div key={i} className="flex gap-3 py-1 border-b border-gray-800/30">
                <span className="text-gray-700 shrink-0 w-36 truncate">{e.ts ?? e.timestamp}</span>
                <span className={`shrink-0 font-bold ${REGIME_COLOUR[e.event_type] ?? "text-indigo-400"}`}>
                  {e.event_type}
                </span>
                <span className="text-gray-500 truncate flex-1">{e.why ?? e.details ?? ""}</span>
              </div>
            ))}
          </div>
        )}
      </div>

    </div>
  );
}
