import React, { Component, useEffect, useRef, useState } from "react";
import IntelligencePanel from "../components/IntelligencePanel";
import PnLPanel from "../components/PnLPanel";
import PositionTable from "../components/PositionTable";
import SignalLog from "../components/SignalLog";
import StrategyRanks from "../components/StrategyRanks";
import { formatCurrency } from "../lib/utils";

// ── Error boundary ────────────────────────────────────────────────────────────

class PanelBoundary extends Component {
  constructor(props) { super(props); this.state = { error: null }; }
  static getDerivedStateFromError(e) { return { error: e }; }
  render() {
    if (this.state.error) {
      return (
        <div className="bg-gray-900 border border-red-900/60 rounded p-3 text-xs text-red-400 font-mono">
          ⚠ {this.props.label ?? "Panel"} error: {this.state.error.message}
        </div>
      );
    }
    return this.props.children;
  }
}

// ── Market Scanner ────────────────────────────────────────────────────────────

const STATUS_CFG = {
  live:         { dot: "bg-green-400 animate-pulse", label: "LIVE",         text: "text-green-400" },
  stale:        { dot: "bg-yellow-500",              label: "STALE",        text: "text-yellow-500" },
  searching:    { dot: "bg-cyan-500 animate-pulse",  label: "SCANNING",     text: "text-cyan-400"  },
  offline:      { dot: "bg-gray-700",                label: "OFFLINE",      text: "text-gray-600"  },
  market_closed:{ dot: "bg-indigo-800",              label: "MKT CLOSED",   text: "text-indigo-500" },
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

  // Render all instruments, live first then stale → market_closed → searching → offline
  const STATUS_ORDER = { live: 0, stale: 1, market_closed: 2, searching: 3, offline: 4 };
  const sortedEntries = [...allEntries].sort(([, a], [, b]) =>
    (STATUS_ORDER[a.status] ?? 3) - (STATUS_ORDER[b.status] ?? 3)
  );
  const currency    = sortedEntries.filter(([, d]) =>  d.is_currency);
  const commodities = sortedEntries.filter(([, d]) =>  d.is_commodity && !d.is_currency);
  const equities    = sortedEntries.filter(([, d]) => !d.is_currency && !d.is_commodity);

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
              ? liveCount > 0 ? `${liveCount} live` : totalCount > 0 ? "no ticks" : "scanning"
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

// ── Opportunities Panel ───────────────────────────────────────────────────────

const DIR_CFG = {
  BUY:  { cls: "text-green-400 border-green-800 bg-green-950/30",  label: "BUY"  },
  SELL: { cls: "text-red-400   border-red-800   bg-red-950/30",    label: "SELL" },
  FLAT: { cls: "text-gray-500  border-gray-700  bg-gray-800/30",   label: "FLAT" },
};

const ASSET_CFG = {
  Equity:   "text-blue-400",
  ETF:      "text-purple-400",
  MCX:      "text-orange-400",
  Currency: "text-cyan-400",
};

function OpportunityRow({ item, onSubscribe }) {
  const dir = DIR_CFG[item.direction] ?? DIR_CFG.FLAT;
  const assetCls = ASSET_CFG[item.asset_class] ?? "text-gray-400";
  return (
    <div className="flex items-center gap-2 py-1.5 border-b border-gray-800/40 last:border-0">
      <span className="text-[9px] text-gray-700 w-4 text-right tabular-nums shrink-0">{item.rank}</span>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-1.5">
          <span className="text-xs font-mono font-bold text-gray-200 truncate">{item.symbol}</span>
          <span className={`text-[8px] font-bold shrink-0 ${assetCls}`}>{item.asset_class}</span>
        </div>
        <div className="text-[9px] text-gray-600 mt-0.5 truncate">{item.reasoning}</div>
      </div>
      <div className="shrink-0 text-right">
        <div className={`text-[9px] font-mono tabular-nums ${item.change_pct >= 0 ? "text-green-400" : "text-red-400"}`}>
          {item.change_pct >= 0 ? "+" : ""}{item.change_pct?.toFixed(2)}%
        </div>
        <div className="text-[9px] text-gray-600 font-mono">score {item.score?.toFixed(0)}</div>
      </div>
      <span className={`shrink-0 text-[8px] font-bold px-1.5 py-0.5 rounded border tracking-widest ${dir.cls}`}>
        {dir.label}
      </span>
      <button
        onClick={() => onSubscribe(item)}
        title="Subscribe to feed"
        className="shrink-0 w-5 h-5 flex items-center justify-center rounded border border-gray-800
                   text-gray-600 hover:text-cyan-400 hover:border-cyan-800 transition-colors text-xs"
      >+</button>
    </div>
  );
}

function OpportunitiesPanel({ snapshot }) {
  const disc        = snapshot?.discovery ?? {};
  const results     = disc.results ?? [];
  const marketOpen  = disc.market_open;
  const scanAgo     = disc.last_scan_ago ?? null;
  const error       = disc.error;
  const [scanning, setScanning] = React.useState(false);

  const scanNow = async () => {
    setScanning(true);
    await fetch("/api/market/discover", { method: "POST" }).catch(() => {});
    setScanning(false);
  };

  const subscribe = async (item) => {
    await fetch("/api/instruments/subscribe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        symbol: item.symbol,
        exchange_segment: item.segment,
        security_id: item.security_id ?? "",
        lot_size: 1,
      }),
    }).catch(() => {});
  };

  const scanAgoStr = scanAgo == null ? "—"
    : scanAgo < 60   ? `${scanAgo}s ago`
    : `${(scanAgo / 60).toFixed(0)}m ago`;

  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-gray-500 tracking-widest uppercase">Opportunities</span>
          {marketOpen === true  && <span className="text-[8px] text-green-400 border border-green-900 bg-green-950/20 px-1.5 py-0.5 rounded font-bold">MARKET OPEN</span>}
          {marketOpen === false && <span className="text-[8px] text-gray-600 border border-gray-800 px-1.5 py-0.5 rounded font-bold">MARKET CLOSED</span>}
        </div>
        <div className="flex items-center gap-2">
          {scanAgo != null && <span className="text-[9px] text-gray-700 font-mono">{scanAgoStr}</span>}
          <button
            onClick={scanNow}
            disabled={scanning}
            className="text-[9px] px-2 py-0.5 rounded border border-cyan-900 text-cyan-700
                       hover:text-cyan-400 hover:border-cyan-700 transition-colors disabled:opacity-40"
          >{scanning ? "scanning…" : "scan now"}</button>
        </div>
      </div>

      {error && (
        <div className="text-[9px] text-yellow-700 bg-yellow-950/20 border border-yellow-900/40 rounded px-2 py-1 mb-2">
          {error}
        </div>
      )}

      {results.length === 0 ? (
        <div className="text-xs text-gray-600 text-center py-4 animate-pulse">
          {scanning ? "Scanning Indian markets…" : "Waiting for first scan…"}
        </div>
      ) : (
        <div>
          <div className="grid grid-cols-[auto_1fr_auto_auto_auto] gap-2 text-[8px] text-gray-700 uppercase tracking-widest pb-1 border-b border-gray-800 mb-1">
            <span>#</span><span>Symbol</span><span>Chg</span><span>Dir</span><span></span>
          </div>
          {results.map((item) => (
            <OpportunityRow key={item.symbol} item={item} onSubscribe={subscribe} />
          ))}
          <div className="text-[9px] text-gray-700 mt-2">
            NSE live data · Nifty 50 + BankNifty + Midcap · scored by momentum, range, trend
          </div>
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
        <PanelBoundary label="Opportunities">
          <OpportunitiesPanel snapshot={snapshot} />
        </PanelBoundary>
        <PanelBoundary label="PnL Chart">
          <PnLPanel
            pnlHistory={pnlHistory.length ? pnlHistory : [{ t: "—", pnl: 0 }]}
            snapshot={snapshot}
          />
        </PanelBoundary>
        <PanelBoundary label="Positions">
          <PositionTable snapshot={snapshot} />
        </PanelBoundary>
        <PanelBoundary label="Signal Log">
          <SignalLog signals={signals} />
        </PanelBoundary>
      </div>
      {/* right 1/3 */}
      <div className="flex flex-col gap-4">
        <PanelBoundary label="AI Pair Selector">
          <IntelligencePanel snapshot={snapshot} />
        </PanelBoundary>
        <PanelBoundary label="Market Scanner">
          <MarketScanner scanner={snapshot?.scanner} connected={connected} onSearchOpen={onSearchOpen} />
        </PanelBoundary>
        <PanelBoundary label="Strategy Ranks">
          <StrategyRanks snapshot={snapshot} />
        </PanelBoundary>
        <PanelBoundary label="System Health">
          <HealthTile snapshot={snapshot} connected={connected} />
        </PanelBoundary>
      </div>
    </div>
  );
}
