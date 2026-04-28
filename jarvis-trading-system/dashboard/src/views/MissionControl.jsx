import { useEffect, useRef, useState } from "react";
import IntelligencePanel from "../components/IntelligencePanel";
import PnLPanel from "../components/PnLPanel";
import PositionTable from "../components/PositionTable";
import SignalLog from "../components/SignalLog";
import StrategyRanks from "../components/StrategyRanks";
import { formatCurrency } from "../lib/utils";

// ── Market Scanner ────────────────────────────────────────────────────────────

const STATUS_CFG = {
  live:      { dot: "bg-green-400 animate-pulse", label: "LIVE",      text: "text-green-400" },
  stale:     { dot: "bg-yellow-500",              label: "STALE",     text: "text-yellow-500" },
  searching: { dot: "bg-cyan-500 animate-pulse",  label: "SCANNING",  text: "text-cyan-400"  },
  offline:   { dot: "bg-gray-700",                label: "OFFLINE",   text: "text-gray-600"  },
};

function PairRow({ sym, data, flash }) {
  const cfg  = STATUS_CFG[data.status] ?? STATUS_CFG.searching;
  const isCurr = data.is_currency;
  const priceStr = data.ltp != null
    ? (isCurr ? data.ltp.toFixed(4) : `₹${data.ltp.toFixed(2)}`)
    : "—";
  const idleStr = data.last_tick_ago != null
    ? data.last_tick_ago < 60
      ? `${data.last_tick_ago.toFixed(0)}s ago`
      : `${(data.last_tick_ago / 60).toFixed(1)}m ago`
    : "—";

  return (
    <div className={[
      "grid gap-2 py-2 border-b border-gray-800/40 last:border-0 transition-colors duration-300",
      "grid-cols-[auto_1fr_auto_auto_auto]",
      flash ? (flash === "up" ? "bg-green-950/20" : "bg-red-950/20") : "",
    ].join(" ")}>
      {/* Status dot */}
      <div className="flex items-center justify-center w-5">
        <span className={`w-2 h-2 rounded-full ${cfg.dot}`} />
      </div>

      {/* Symbol + status */}
      <div className="min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="text-xs font-mono font-bold text-gray-200">{sym}</span>
          <span className={`text-[8px] font-bold tracking-widest ${cfg.text}`}>{cfg.label}</span>
        </div>
        <div className="text-[9px] text-gray-700 mt-0.5">
          {data.ticks > 0 ? `${data.ticks} ticks` : "waiting…"}
          {data.last_tick_ago != null && <span className="ml-1">· {idleStr}</span>}
        </div>
      </div>

      {/* Price */}
      <div className={`text-xs font-mono tabular-nums self-center ${cfg.text}`}>
        {priceStr}
      </div>

      {/* Direction arrow */}
      <div className="self-center w-3 text-center">
        {flash === "up"   && <span className="text-green-400 text-[10px]">▲</span>}
        {flash === "down" && <span className="text-red-400   text-[10px]">▼</span>}
      </div>

      {/* Scanning badge */}
      <div className="self-center">
        {data.status === "live" && (
          <span className="text-[8px] px-1.5 py-0.5 rounded border border-cyan-900 text-cyan-700 bg-cyan-950/30 font-bold tracking-widest">
            TRADING
          </span>
        )}
      </div>
    </div>
  );
}

function MarketScanner({ scanner, connected, onSearchOpen }) {
  const prevRef  = useRef({});
  const [flash, setFlash] = useState({});

  useEffect(() => {
    if (!scanner) return;
    const next = {};
    Object.entries(scanner).forEach(([sym, d]) => {
      const prev = prevRef.current[sym]?.ltp;
      if (prev != null && d.ltp != null && prev !== d.ltp) {
        next[sym] = d.ltp > prev ? "up" : "down";
      }
    });
    prevRef.current = scanner;
    if (!Object.keys(next).length) return;
    setFlash(next);
    const t = setTimeout(() => setFlash({}), 700);
    return () => clearTimeout(t);
  }, [scanner]);

  const allEntries  = Object.entries(scanner ?? {});
  const liveEntries = allEntries.filter(([, d]) => d.status === "live");
  const liveCount   = liveEntries.length;
  const totalCount  = allEntries.length;

  // Only render pairs that are actually receiving ticks right now
  const currency    = liveEntries.filter(([, d]) =>  d.is_currency);
  const commodities = liveEntries.filter(([, d]) =>  d.is_commodity && !d.is_currency);
  const equities    = liveEntries.filter(([, d]) => !d.is_currency && !d.is_commodity);

  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-3">
        <span className="text-[10px] text-gray-500 tracking-widest uppercase">
          Market Scanner
        </span>
        <div className="flex items-center gap-2">
          <span className={`w-1.5 h-1.5 rounded-full ${connected ? "bg-green-500 animate-pulse" : "bg-gray-600"}`} />
          <span className="text-[9px] text-gray-600 font-mono">
            {connected
              ? liveCount > 0 ? `${liveCount} live` : totalCount > 0 ? "market closed" : "scanning"
              : "disconnected"}
          </span>
          {onSearchOpen && (
            <button
              onClick={onSearchOpen}
              title="Add instrument"
              className="w-6 h-6 flex items-center justify-center rounded border border-gray-800
                         text-gray-600 hover:text-cyan-400 hover:border-cyan-800 transition-colors"
            >
              <svg className="w-3 h-3" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
                <path strokeLinecap="round" d="M12 5v14M5 12h14" />
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* Summary bar — only when trading */}
      {liveCount > 0 && (
        <div className="bg-cyan-950/20 border border-cyan-900/40 rounded px-2 py-1 mb-3">
          <span className="text-[9px] text-cyan-600">
            Auto-trading: {liveEntries.map(([s]) => s).join(", ")}
          </span>
        </div>
      )}

      {totalCount === 0 ? (
        <div className="text-xs text-gray-600 text-center py-4 animate-pulse">
          Discovering available pairs…
        </div>
      ) : liveCount === 0 ? (
        <div className="py-4 text-center space-y-1">
          <div className="text-xs text-gray-600">Market closed</div>
          <div className="text-[9px] text-gray-700">
            {totalCount} pair{totalCount !== 1 ? "s" : ""} subscribed · waiting for ticks
          </div>
        </div>
      ) : (
        <>
          {currency.length > 0 && (
            <>
              <div className="text-[9px] text-gray-700 uppercase tracking-widest mb-1">
                Currency Futures
              </div>
              {currency.map(([sym, data]) => (
                <PairRow key={sym} sym={sym} data={data} flash={flash[sym]} />
              ))}
            </>
          )}
          {commodities.length > 0 && (
            <div className={currency.length ? "mt-3" : ""}>
              <div className="text-[9px] text-gray-700 uppercase tracking-widest mb-1">
                Commodities · MCX
              </div>
              {commodities.map(([sym, data]) => (
                <PairRow key={sym} sym={sym} data={data} flash={flash[sym]} />
              ))}
            </div>
          )}
          {equities.length > 0 && (
            <div className={(currency.length || commodities.length) ? "mt-3" : ""}>
              <div className="text-[9px] text-gray-700 uppercase tracking-widest mb-1">
                Equities
              </div>
              {equities.map(([sym, data]) => (
                <PairRow key={sym} sym={sym} data={data} flash={flash[sym]} />
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── System Health ─────────────────────────────────────────────────────────────

function HealthTile({ snapshot, connected }) {
  const broker = snapshot?.broker ?? {};
  const scanner = snapshot?.scanner ?? {};
  const liveCount = Object.values(scanner).filter((d) => d.status === "live").length;

  const items = [
    { label: "FEED",      val: connected ? "LIVE" : "RECONNECTING", ok: connected },
    { label: "SCANNING",  val: liveCount ? `${liveCount} pairs` : "0 pairs", ok: liveCount > 0 },
    { label: "POSITIONS", val: Object.keys(broker.open_positions ?? {}).length, ok: true },
    { label: "KILL SW",   val: broker.kill_switch_active ? "ACTIVE" : "ARMED", ok: !broker.kill_switch_active },
    { label: "CAPITAL",   val: formatCurrency(broker.capital ?? 0), ok: true },
    { label: "DAY P&L",
      val: `${(broker.daily_pnl ?? 0) >= 0 ? "+" : ""}${formatCurrency(broker.daily_pnl ?? 0)}`,
      ok: (broker.daily_pnl ?? 0) >= 0 },
  ];

  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-4">
      <div className="text-[10px] text-gray-500 tracking-widest uppercase mb-3">System Health</div>
      <div className="grid grid-cols-2 gap-x-4">
        {items.map(({ label, val, ok }) => (
          <div key={label} className="flex justify-between items-center py-1 border-b border-gray-800/40">
            <span className="text-[10px] text-gray-600">{label}</span>
            <span className={`text-[10px] font-bold font-mono ${ok ? "text-green-400" : "text-red-400"}`}>
              {val}
            </span>
          </div>
        ))}
      </div>
      {snapshot?.regime_features && Object.keys(snapshot.regime_features).length > 0 && (
        <div className="mt-3">
          <div className="text-[9px] text-gray-700 tracking-widest uppercase mb-1">Regime features</div>
          {Object.entries(snapshot.regime_features).slice(0, 4).map(([k, v]) => (
            <div key={k} className="flex justify-between text-[10px] py-0.5">
              <span className="text-gray-700 truncate max-w-[120px]">{k}</span>
              <span className="text-gray-500 font-mono tabular-nums">
                {typeof v === "number" ? v.toFixed(3) : String(v)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Main view ─────────────────────────────────────────────────────────────────

export default function MissionControl({ snapshot, pnlHistory, signals, connected, onSearchOpen }) {
  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4 p-4">
      {/* left 2/3 */}
      <div className="lg:col-span-2 flex flex-col gap-4">
        <PnLPanel
          pnlHistory={pnlHistory.length ? pnlHistory : [{ t: "—", pnl: 0 }]}
          snapshot={snapshot}
        />
        <PositionTable snapshot={snapshot} />
        <SignalLog signals={signals} />
      </div>
      {/* right 1/3 */}
      <div className="flex flex-col gap-4">
        <IntelligencePanel snapshot={snapshot} />
        <MarketScanner scanner={snapshot?.scanner} connected={connected} onSearchOpen={onSearchOpen} />
        <StrategyRanks snapshot={snapshot} />
        <HealthTile snapshot={snapshot} connected={connected} />
      </div>
    </div>
  );
}
