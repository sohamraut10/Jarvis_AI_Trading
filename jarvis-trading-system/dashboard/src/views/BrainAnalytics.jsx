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

// ── Market Sentinel panel ─────────────────────────────────────────────────────

function SentimentBadge({ sentiment }) {
  const styles = {
    bullish: "text-green-400 border-green-800 bg-green-950/30",
    bearish: "text-red-400 border-red-800 bg-red-950/30",
    neutral: "text-yellow-400 border-yellow-800 bg-yellow-950/30",
  };
  const s = (sentiment ?? "neutral").toLowerCase();
  return (
    <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded border tracking-wide ${styles[s] ?? styles.neutral}`}>
      {s.toUpperCase()}
    </span>
  );
}

function CandidateRow({ c }) {
  return (
    <div className="flex items-center gap-2 py-1 border-b border-gray-800/40">
      <span className="font-mono text-xs text-gray-200 w-24 truncate">{c.symbol}</span>
      <DirBadge direction={c.direction} />
      <ConvictionBar conviction={c.conviction} />
      <span className="text-[9px] text-gray-500 flex-1 truncate">{c.reason}</span>
    </div>
  );
}

function SentinelPanel({ sentinel, onRefresh }) {
  const [refreshing, setRefreshing] = useState(false);

  const refresh = async () => {
    setRefreshing(true);
    try {
      await fetch("/api/sentinel/refresh", { method: "POST" });
    } finally {
      setRefreshing(false);
    }
  };

  if (!sentinel) {
    return (
      <div className="bg-gray-900 border border-gray-800 rounded p-4">
        <div className="flex items-center justify-between mb-3">
          <span className="text-[10px] text-gray-500 tracking-widest uppercase">Market Sentinel</span>
          <button onClick={refresh} disabled={refreshing}
            className="text-[9px] text-cyan-700 hover:text-cyan-400 transition-colors disabled:opacity-40">
            {refreshing ? "SCANNING…" : "SCAN NOW"}
          </button>
        </div>
        <div className="text-center text-gray-700 text-xs py-6">— waiting for first scan —</div>
      </div>
    );
  }

  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-gray-500 tracking-widest uppercase">Market Sentinel</span>
          <SentimentBadge sentiment={sentinel.overall_sentiment} />
        </div>
        <div className="flex items-center gap-3">
          <span className="text-[9px] text-gray-600 font-mono">{fmtTs(sentinel.ts)}</span>
          <button onClick={refresh} disabled={refreshing}
            className="text-[9px] text-cyan-700 hover:text-cyan-400 transition-colors disabled:opacity-40">
            {refreshing ? "…" : "REFRESH"}
          </button>
        </div>
      </div>

      {/* Regime commentary */}
      {sentinel.regime_commentary && (
        <p className="text-[10px] text-gray-400 italic mb-3">{sentinel.regime_commentary}</p>
      )}

      {/* Top candidates */}
      <div className="mb-3">
        <div className="text-[9px] text-gray-600 uppercase tracking-widest mb-1">Top Candidates</div>
        {(sentinel.top_candidates ?? []).length === 0 ? (
          <div className="text-[10px] text-gray-700">No high-conviction candidates</div>
        ) : (
          <div>
            {sentinel.top_candidates.map((c, i) => <CandidateRow key={i} c={c} />)}
          </div>
        )}
      </div>

      {/* Themes + Risk flags */}
      <div className="grid grid-cols-2 gap-3">
        <div>
          <div className="text-[9px] text-gray-600 uppercase tracking-widest mb-1">Themes</div>
          {(sentinel.themes ?? []).length === 0 ? (
            <span className="text-[9px] text-gray-700">—</span>
          ) : (
            <ul className="space-y-0.5">
              {sentinel.themes.map((t, i) => (
                <li key={i} className="text-[9px] text-cyan-400 before:content-['•'] before:mr-1">{t}</li>
              ))}
            </ul>
          )}
        </div>
        <div>
          <div className="text-[9px] text-gray-600 uppercase tracking-widest mb-1">Risk Flags</div>
          {(sentinel.risk_flags ?? []).length === 0 ? (
            <span className="text-[9px] text-gray-700">None</span>
          ) : (
            <ul className="space-y-0.5">
              {sentinel.risk_flags.map((f, i) => (
                <li key={i} className="text-[9px] text-yellow-500 before:content-['!'] before:mr-1">{f}</li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <div className="mt-2 text-[8px] text-gray-700">model: {sentinel.model_used}  cost: ${sentinel.cost_usd?.toFixed(5)}</div>
    </div>
  );
}

// ── Position Guardian alerts ───────────────────────────────────────────────────

const ACTION_STYLE = {
  HOLD:          "text-green-400 border-green-900 bg-green-950/20",
  TIGHTEN_STOP:  "text-yellow-400 border-yellow-900 bg-yellow-950/20",
  PARTIAL_EXIT:  "text-orange-400 border-orange-900 bg-orange-950/20",
  FULL_EXIT:     "text-red-400 border-red-900 bg-red-950/20",
};

function GuardianRow({ r }) {
  const style = ACTION_STYLE[r.action] ?? ACTION_STYLE.HOLD;
  return (
    <div className="flex items-start gap-2 py-1.5 border-b border-gray-800/40">
      <span className="font-mono text-xs text-gray-200 w-24 shrink-0 truncate">{r.symbol}</span>
      <span className={`text-[8px] font-bold px-1.5 py-0.5 rounded border tracking-wide shrink-0 ${style}`}>
        {r.action.replace("_", " ")}
      </span>
      <span className="text-[9px] text-gray-500 flex-1 min-w-0 truncate">{r.reasoning}</span>
      {r.auto_executed && (
        <span className="text-[8px] font-bold text-red-500 shrink-0">AUTO</span>
      )}
      <span className="text-[9px] text-gray-700 shrink-0">{fmtTs(r.ts)}</span>
    </div>
  );
}

function GuardianAlerts({ guardian }) {
  const reviews     = guardian?.recent_reviews ?? [];
  const autoExecute = guardian?.auto_execute ?? false;
  const nonHold     = reviews.filter((r) => r.action !== "HOLD");

  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-[10px] text-gray-500 tracking-widest uppercase">Position Guardian</span>
        <div className="flex items-center gap-2">
          {autoExecute && (
            <span className="text-[8px] font-bold text-red-400 border border-red-900 bg-red-950/20 px-1.5 py-0.5 rounded tracking-wide">
              AUTO-EXECUTE ON
            </span>
          )}
          <span className="text-[9px] text-gray-600">{reviews.length} reviews</span>
        </div>
      </div>

      {nonHold.length === 0 ? (
        <div className="text-center text-gray-700 text-xs py-4">— all positions HOLD —</div>
      ) : (
        <div className="max-h-48 overflow-y-auto">
          {nonHold.slice().reverse().map((r, i) => <GuardianRow key={i} r={r} />)}
        </div>
      )}

      {reviews.length > 0 && nonHold.length < reviews.length && (
        <div className="text-[9px] text-gray-700 mt-2 text-center">
          {reviews.length - nonHold.length} positions on HOLD
        </div>
      )}
    </div>
  );
}

// ── Meta Advisor panel ────────────────────────────────────────────────────────

function MetaAdvisorPanel({ metaAdvisor }) {
  const [accepting, setAccepting] = useState({});
  const [accepted,  setAccepted]  = useState({});

  const suggestions  = metaAdvisor?.suggestions ?? [];
  const summary      = metaAdvisor?.last_result_summary;

  const accept = async (param) => {
    setAccepting((p) => ({ ...p, [param]: true }));
    try {
      await fetch("/api/meta_advisor/accept", {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ parameter: param }),
      });
      setAccepted((p) => ({ ...p, [param]: true }));
    } finally {
      setAccepting((p) => ({ ...p, [param]: false }));
    }
  };

  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-[10px] text-gray-500 tracking-widest uppercase">MetaAdvisor</span>
        <span className="text-[9px] text-gray-600">
          {suggestions.length} suggestion{suggestions.length !== 1 ? "s" : ""}
        </span>
      </div>

      {summary && (
        <p className="text-[9px] text-gray-400 italic mb-3 border-l-2 border-cyan-800 pl-2">{summary}</p>
      )}

      {suggestions.length === 0 ? (
        <div className="text-center text-gray-700 text-xs py-4">— no suggestions yet — (runs every 30 min) —</div>
      ) : (
        <div className="space-y-2">
          {suggestions.map((s, i) => (
            <div key={i} className="bg-gray-800/50 border border-gray-700 rounded p-2.5">
              <div className="flex items-center justify-between gap-2 mb-1">
                <span className="text-xs font-mono text-gray-200 font-bold">{s.parameter}</span>
                <div className="flex items-center gap-2">
                  <span className="text-[9px] text-gray-500">
                    {String(s.current)} → <span className="text-cyan-400 font-bold">{String(s.suggested)}</span>
                  </span>
                  <span className="text-[8px] text-gray-600">conf: {s.confidence}</span>
                </div>
              </div>
              <p className="text-[9px] text-gray-500 mb-2">{s.rationale}</p>
              <button
                onClick={() => accept(s.parameter)}
                disabled={accepting[s.parameter] || accepted[s.parameter] || s.accepted}
                className={[
                  "text-[9px] font-bold px-2.5 py-1 rounded border transition-all",
                  accepted[s.parameter] || s.accepted
                    ? "border-green-800 bg-green-950/30 text-green-500 cursor-not-allowed"
                    : accepting[s.parameter]
                    ? "border-gray-700 text-gray-600 cursor-wait"
                    : "border-cyan-800 bg-cyan-950/20 text-cyan-400 hover:bg-cyan-900/30",
                ].join(" ")}
              >
                {accepted[s.parameter] || s.accepted ? "✓ ACCEPTED" : accepting[s.parameter] ? "…" : "ACCEPT"}
              </button>
            </div>
          ))}
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

      {/* ── Market Sentinel ──────────────────────────────────────────────── */}
      <div className="lg:col-span-2">
        <SentinelPanel sentinel={snapshot?.sentinel} />
      </div>

      {/* ── Position Guardian ────────────────────────────────────────────── */}
      <div>
        <GuardianAlerts guardian={snapshot?.guardian} />
      </div>

      {/* ── MetaAdvisor ──────────────────────────────────────────────────── */}
      <div>
        <MetaAdvisorPanel metaAdvisor={snapshot?.meta_advisor} />
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
