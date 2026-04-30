import { useEffect, useRef, useState } from "react";

const STRATEGY_DISPLAY = {
  ema_crossover: { label: "EMA Cross",    icon: "⟋",  tf: "5m" },
  supertrend:    { label: "SuperTrend",   icon: "⬡",  tf: "5m" },
  orb_breakout:  { label: "ORB Break",    icon: "◈",  tf: "15m" },
  rsi_momentum:  { label: "RSI Mom",      icon: "◎",  tf: "5m" },
  vwap_breakout: { label: "VWAP Break",   icon: "⟁",  tf: "15m" },
};

const STATE_CFG = {
  SIGNAL:   { label: "SIGNAL",   bg: "bg-cyan-950/60",  border: "border-cyan-600",   dot: "bg-cyan-400 animate-ping",   text: "text-cyan-400"  },
  HUNTING:  { label: "HUNTING",  bg: "bg-gray-900",     border: "border-gray-700",   dot: "bg-green-500 animate-pulse", text: "text-green-400" },
  BLOCKED:  { label: "BLOCKED",  bg: "bg-gray-900",     border: "border-yellow-900", dot: "bg-yellow-600",              text: "text-yellow-600"},
  DISABLED: { label: "DISABLED", bg: "bg-gray-950",     border: "border-gray-800",   dot: "bg-gray-700",                text: "text-gray-700"  },
  IDLE:     { label: "IDLE",     bg: "bg-gray-900",     border: "border-gray-800",   dot: "bg-gray-600",                text: "text-gray-500"  },
};

const SIDE_COLOR = { BUY: "text-green-400", SELL: "text-red-400" };

function timeSince(isoStr) {
  if (!isoStr) return null;
  const diff = (Date.now() - new Date(isoStr).getTime()) / 1000;
  if (diff < 60)   return `${Math.floor(diff)}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  return `${Math.floor(diff / 3600)}h ago`;
}

function ScanLine() {
  return (
    <div className="relative w-full h-0.5 bg-gray-800 overflow-hidden rounded-full mt-2">
      <div
        className="absolute top-0 h-full w-1/3 bg-gradient-to-r from-transparent via-cyan-500/60 to-transparent"
        style={{ animation: "scanline 2s linear infinite" }}
      />
    </div>
  );
}

function StrategyCard({ name, state, allocation, last, sharpe, winRate, totalTrades }) {
  const cfg  = STATE_CFG[state] ?? STATE_CFG.IDLE;
  const meta = STRATEGY_DISPLAY[name] ?? { label: name, icon: "◇", tf: "?" };
  const [flash, setFlash] = useState(false);
  const prevSig = useRef(null);

  useEffect(() => {
    const sig = last?.t;
    if (sig && sig !== prevSig.current) {
      setFlash(true);
      const t = setTimeout(() => setFlash(false), 1200);
      prevSig.current = sig;
      return () => clearTimeout(t);
    }
  }, [last]);

  const allocPct = Math.round((allocation ?? 0) * 100);
  const sharpeColor = sharpe == null ? "text-gray-600" : sharpe > 0.5 ? "text-green-400" : sharpe < 0 ? "text-red-400" : "text-yellow-400";

  return (
    <div className={`relative rounded border p-3 transition-all duration-300 ${cfg.bg} ${cfg.border} ${flash ? "ring-1 ring-cyan-500/50" : ""}`}>
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-1.5">
          <span className="relative flex h-2 w-2">
            <span className={`relative inline-flex rounded-full h-2 w-2 ${cfg.dot}`} />
          </span>
          <span className="text-[9px] font-mono text-gray-200 font-bold">{meta.label}</span>
          <span className="text-[8px] text-gray-700">{meta.tf}</span>
        </div>
        <span className={`text-[8px] font-bold tracking-widest ${cfg.text}`}>{cfg.label}</span>
      </div>

      {/* Last signal */}
      {last ? (
        <div className="flex items-center gap-1.5 mb-1">
          <span className={`text-[9px] font-bold ${SIDE_COLOR[last.side] ?? "text-gray-400"}`}>{last.side}</span>
          <span className="text-[9px] text-gray-300 font-mono truncate max-w-[70px]">{last.sym}</span>
          <span className={`ml-auto text-[8px] ${last.approved ? "text-green-700" : "text-red-900"}`}>
            {last.approved ? "✓" : "✗"}
          </span>
        </div>
      ) : (
        <div className="text-[8px] text-gray-700 mb-1">no signals yet</div>
      )}
      {last?.t && <div className="text-[8px] text-gray-700">{timeSince(last.t)}</div>}

      {/* Scan line animation for hunting state */}
      {state === "HUNTING" && <ScanLine />}

      {/* Stats footer */}
      <div className="flex items-center justify-between mt-2 pt-2 border-t border-gray-800/60">
        <div className="flex flex-col">
          <span className="text-[8px] text-gray-700">Alloc</span>
          <span className="text-[9px] font-mono text-gray-400">{allocPct}%</span>
        </div>
        <div className="flex flex-col items-center">
          <span className="text-[8px] text-gray-700">Win</span>
          <span className="text-[9px] font-mono text-gray-400">
            {winRate != null ? `${Math.round(winRate * 100)}%` : "—"}
          </span>
        </div>
        <div className="flex flex-col items-end">
          <span className="text-[8px] text-gray-700">Sharpe</span>
          <span className={`text-[9px] font-mono ${sharpeColor}`}>
            {sharpe != null ? sharpe.toFixed(2) : "—"}
          </span>
        </div>
      </div>

      {/* Allocation bar */}
      <div className="mt-1.5 w-full bg-gray-800 rounded-full h-0.5 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-1000 ${state === "DISABLED" ? "bg-gray-700" : "bg-cyan-600"}`}
          style={{ width: `${Math.min(allocPct, 100)}%` }}
        />
      </div>
    </div>
  );
}

function deriveState(stratState, signals, name) {
  if (!stratState) return "IDLE";
  if (stratState.disabled) return "DISABLED";
  if (!stratState.active_regime) return "BLOCKED";
  // Check if this strategy fired a signal recently (within 45s)
  const last = stratState.last;
  if (last?.t) {
    const age = (Date.now() - new Date(last.t).getTime()) / 1000;
    if (age < 45) return "SIGNAL";
  }
  if ((stratState.allocation ?? 0) > 0) return "HUNTING";
  return "IDLE";
}

export default function StrategyHuntCards({ snapshot, signals }) {
  const sys   = snapshot?.system ?? {};
  const states = sys.strategy_states ?? {};
  const sharpes = snapshot?.sharpes ?? {};
  const stratStats = snapshot?.strategy_stats ?? {};

  const names = Object.keys({ ...states, ...sharpes });
  if (names.length === 0) return (
    <div className="bg-gray-900 border border-gray-800 rounded p-4">
      <div className="text-[10px] text-gray-500 tracking-widest uppercase mb-2">Strategy Hunt</div>
      <div className="text-xs text-gray-700 py-4 text-center animate-pulse">waiting for strategies…</div>
    </div>
  );

  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-4">
      <div className="flex items-center justify-between mb-3">
        <span className="text-[10px] text-gray-500 tracking-widest uppercase">Strategy Hunt</span>
        <span className="text-[9px] text-gray-700 font-mono">
          {names.filter((n) => deriveState(states[n], signals, n) === "HUNTING").length} hunting
        </span>
      </div>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
        {names.map((name) => {
          const st = states[name] ?? {};
          const state = deriveState(st, signals, name);
          const stats = stratStats[name] ?? {};
          return (
            <StrategyCard
              key={name}
              name={name}
              state={state}
              allocation={st.allocation}
              last={st.last}
              sharpe={sharpes[name] ?? null}
              winRate={stats.win_rate ?? null}
              totalTrades={stats.total_trades ?? 0}
            />
          );
        })}
      </div>
    </div>
  );
}
