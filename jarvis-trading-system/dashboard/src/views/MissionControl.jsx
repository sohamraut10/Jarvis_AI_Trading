import PnLPanel from "../components/PnLPanel";
import PositionTable from "../components/PositionTable";
import SignalLog from "../components/SignalLog";
import StrategyRanks from "../components/StrategyRanks";
import { enumValue, formatCurrency } from "../lib/utils";

function HealthTile({ snapshot, connected }) {
  const broker = snapshot?.broker ?? {};
  const items = [
    { label: "WS",        val: connected ? "LIVE" : "RECONNECTING", ok: connected },
    { label: "CAPITAL",   val: formatCurrency(broker.capital ?? 0), ok: true },
    { label: "POSITIONS", val: Object.keys(broker.open_positions ?? {}).length, ok: true },
    { label: "KILL SW",   val: broker.kill_switch_active ? "ACTIVE" : "ARMED", ok: !broker.kill_switch_active },
  ];
  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-4">
      <div className="text-[10px] text-gray-500 tracking-widest uppercase mb-3">System Health</div>
      <div className="grid grid-cols-2 gap-2">
        {items.map(({ label, val, ok }) => (
          <div key={label} className="flex justify-between items-center py-1 border-b border-gray-800/40">
            <span className="text-[10px] text-gray-600">{label}</span>
            <span className={`text-[10px] font-bold font-mono ${ok ? "text-green-400" : "text-red-400"}`}>{val}</span>
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

export default function MissionControl({ snapshot, pnlHistory, signals, connected }) {
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
        <StrategyRanks snapshot={snapshot} />
        <HealthTile snapshot={snapshot} connected={connected} />
      </div>
    </div>
  );
}
