import { useState } from "react";
import RegimeBadge from "./RegimeBadge";
import { formatCurrency, pnlClass } from "../lib/utils";

export default function HUD({ snapshot, connected, onKill }) {
  const [confirming, setConfirming] = useState(false);
  const broker = snapshot?.broker ?? {};
  const regime = snapshot?.regime ?? "UNKNOWN";
  const pnl = broker.daily_pnl ?? 0;
  const killed = broker.kill_switch_active ?? false;

  function handleKill() {
    if (!confirming) {
      setConfirming(true);
      setTimeout(() => setConfirming(false), 3000);
      return;
    }
    setConfirming(false);
    onKill();
  }

  return (
    <header className="flex items-center gap-3 px-4 py-2 border-b border-gray-800 bg-gray-950 shrink-0">
      {/* Brand */}
      <span className="text-cyan-400 font-bold tracking-[0.3em] text-lg shrink-0">
        JARVIS
      </span>

      <RegimeBadge regime={regime} />

      {/* WS status */}
      <span className={`text-xs shrink-0 ${connected ? "text-green-500" : "text-red-500"}`}>
        {connected ? "● LIVE" : "○ RECONNECTING"}
      </span>

      <div className="flex-1" />

      {/* Capital */}
      <Metric label="CAPITAL" value={formatCurrency(broker.capital ?? 0)} />

      {/* Portfolio */}
      <Metric
        label="PORTFOLIO"
        value={formatCurrency(broker.portfolio_value ?? 0)}
      />

      {/* Daily P&L */}
      <Metric
        label="DAILY P&L"
        value={formatCurrency(pnl, true)}
        valueClass={pnlClass(pnl)}
      />

      {/* Kill switch */}
      <button
        onClick={handleKill}
        disabled={killed}
        className={[
          "px-3 py-1.5 text-xs font-bold rounded border tracking-widest transition-all shrink-0 select-none",
          killed
            ? "border-gray-700 text-gray-600 cursor-not-allowed"
            : confirming
            ? "border-red-500 bg-red-900/60 text-red-300 animate-pulse"
            : "border-red-800 text-red-500 hover:border-red-600 hover:bg-red-950 active:scale-95",
        ].join(" ")}
      >
        {killed ? "HALTED" : confirming ? "CONFIRM?" : "KILL"}
      </button>
    </header>
  );
}

function Metric({ label, value, valueClass = "text-gray-100" }) {
  return (
    <div className="text-right shrink-0">
      <div className="text-[10px] text-gray-600 tracking-widest">{label}</div>
      <div className={`text-sm font-bold tabular-nums ${valueClass}`}>{value}</div>
    </div>
  );
}
