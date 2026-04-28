import { useCallback, useEffect, useRef, useState } from "react";
import HUD from "./components/HUD";
import PnLPanel from "./components/PnLPanel";
import PositionTable from "./components/PositionTable";
import SignalLog from "./components/SignalLog";
import StrategyRanks from "./components/StrategyRanks";
import useWebSocket from "./hooks/useWebSocket";

const WS_URL =
  import.meta.env.VITE_WS_URL ??
  `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws`;
const MAX_PNL_HISTORY = 300;
const MAX_SIGNALS = 100;

export default function App() {
  const { snapshot, connected, send } = useWebSocket(WS_URL);
  const [pnlHistory, setPnlHistory] = useState([]);
  const [signals, setSignals] = useState([]);
  const seenSignalsRef = useRef(new Set());

  // Build rolling P&L history from each snapshot
  useEffect(() => {
    if (!snapshot) return;
    const pnl = snapshot.broker?.daily_pnl ?? 0;
    const t = new Date().toLocaleTimeString("en-IN", { hour12: false });
    setPnlHistory((prev) => {
      const next = [...prev, { t, pnl }];
      return next.length > MAX_PNL_HISTORY ? next.slice(-MAX_PNL_HISTORY) : next;
    });

    // Collect new signals from snapshot
    const incoming = snapshot.recent_signals ?? [];
    setSignals((prev) => {
      const seen = seenSignalsRef.current;
      const fresh = incoming.filter((s) => {
        const key = `${s.t}:${s.symbol}:${s.side}:${s.strategy}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
      if (!fresh.length) return prev;
      const next = [...prev, ...fresh];
      return next.length > MAX_SIGNALS ? next.slice(-MAX_SIGNALS) : next;
    });
  }, [snapshot]);

  const handleKill = useCallback(() => {
    send({ type: "kill_switch" });
  }, [send]);

  if (!snapshot && !connected) {
    return (
      <div className="min-h-screen bg-gray-950 text-gray-100 flex items-center justify-center">
        <p className="text-cyan-400 font-mono text-xl tracking-widest animate-pulse">
          JARVIS TRADING COMMAND CENTER — CONNECTING…
        </p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      <HUD snapshot={snapshot} connected={connected} onKill={handleKill} />

      <main className="flex-1 grid grid-cols-1 lg:grid-cols-3 gap-4 p-4 overflow-auto">
        {/* Left column */}
        <div className="lg:col-span-2 flex flex-col gap-4">
          <PnLPanel
            pnlHistory={pnlHistory.length ? pnlHistory : [{ t: "—", pnl: 0 }]}
            snapshot={snapshot}
          />
          <PositionTable snapshot={snapshot} />
          <SignalLog signals={signals} />
        </div>

        {/* Right column */}
        <div className="flex flex-col gap-4">
          <StrategyRanks snapshot={snapshot} />

          {/* System stats tile */}
          {snapshot && (
            <div className="bg-gray-900 border border-gray-800 rounded p-4 text-xs space-y-2">
              <div className="text-[10px] text-gray-500 tracking-widest uppercase mb-2">
                System
              </div>
              <Stat label="Capital" value={`₹${(snapshot.broker?.capital ?? 0).toFixed(2)}`} />
              <Stat
                label="Portfolio"
                value={`₹${(snapshot.broker?.portfolio_value ?? 0).toFixed(2)}`}
              />
              <Stat
                label="Kill threshold"
                value={`₹${(snapshot.broker?.kill_threshold ?? 0).toFixed(2)}`}
              />
              <Stat label="Open positions" value={Object.keys(snapshot.positions ?? {}).length} />
              <Stat label="Regime" value={snapshot.regime ?? "—"} />
            </div>
          )}
        </div>
      </main>
    </div>
  );
}

function Stat({ label, value }) {
  return (
    <div className="flex justify-between">
      <span className="text-gray-600">{label}</span>
      <span className="text-gray-300 tabular-nums font-mono">{value}</span>
    </div>
  );
}
