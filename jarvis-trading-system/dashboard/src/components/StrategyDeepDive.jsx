import { Area, AreaChart, ResponsiveContainer } from "recharts";
import { formatCurrency, pnlClass } from "../lib/utils";

function Stat({ label, value, valueClass = "text-gray-300" }) {
  return (
    <div className="flex justify-between items-baseline py-1 border-b border-gray-800/40">
      <span className="text-[10px] text-gray-600 tracking-widest uppercase">{label}</span>
      <span className={`text-xs font-mono tabular-nums ${valueClass}`}>{value ?? "—"}</span>
    </div>
  );
}

export default function StrategyDeepDive({ strategy, stats, sharpe, allocation, signals, onClose }) {
  if (!strategy) return null;

  const recentPnls = signals
    .filter((s) => s.strategy === strategy)
    .slice(-20)
    .map((s, i) => ({ i, pnl: s.pnl ?? 0 }));

  const alloc = ((allocation ?? 0) * 100).toFixed(1);
  const colour = sharpe >= 0.5 ? "#4ade80" : sharpe < 0 ? "#f87171" : "#facc15";

  return (
    <div className="fixed inset-0 z-40 flex">
      <div className="flex-1 bg-black/60" onClick={onClose} />
      <div className="w-80 bg-gray-950 border-l border-gray-800 flex flex-col overflow-y-auto">
        {/* header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800 shrink-0">
          <span className="font-bold tracking-widest text-cyan-300 uppercase text-sm">{strategy}</span>
          <button onClick={onClose} className="text-gray-600 hover:text-gray-300 text-lg leading-none">✕</button>
        </div>

        <div className="p-4 space-y-4">
          {/* mini sparkline */}
          {recentPnls.length > 1 && (
            <div>
              <div className="text-[9px] text-gray-700 tracking-widest uppercase mb-1">Recent P&L signal</div>
              <ResponsiveContainer width="100%" height={60}>
                <AreaChart data={recentPnls}>
                  <Area type="monotone" dataKey="pnl" stroke={colour} strokeWidth={1.5}
                    fill={colour} fillOpacity={0.1} dot={false} isAnimationActive={false} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* stats */}
          <div>
            <div className="text-[9px] text-gray-700 tracking-widest uppercase mb-1">Performance</div>
            <Stat label="Allocation"  value={`${alloc}%`} />
            <Stat label="Sharpe"      value={sharpe != null ? sharpe.toFixed(3) : null}
              valueClass={sharpe >= 0 ? "text-green-400" : "text-red-400"} />
            <Stat label="Win rate"    value={stats?.win_rate != null ? `${(stats.win_rate * 100).toFixed(1)}%` : null} />
            <Stat label="Avg win"     value={formatCurrency(stats?.avg_win)} valueClass="text-green-400" />
            <Stat label="Avg loss"    value={formatCurrency(stats?.avg_loss)} valueClass="text-red-400" />
            <Stat label="Total trades" value={stats?.total_trades} />
          </div>

          {/* recent signals for this strategy */}
          <div>
            <div className="text-[9px] text-gray-700 tracking-widest uppercase mb-1">Recent signals</div>
            {signals.filter((s) => s.strategy === strategy).slice(-5).reverse().map((sig, i) => (
              <div key={i} className="flex items-center gap-2 py-1 text-[10px] font-mono border-b border-gray-800/30">
                <span className="text-gray-700 w-16 truncate">{sig.t}</span>
                <span className={sig.side === "BUY" ? "text-green-400" : "text-red-400"}>{sig.side}</span>
                <span className="text-gray-400 truncate">{sig.symbol}</span>
                {sig.price != null && <span className="ml-auto text-gray-600">₹{sig.price.toFixed(2)}</span>}
              </div>
            ))}
            {!signals.filter((s) => s.strategy === strategy).length && (
              <div className="text-[10px] text-gray-700 py-2">— no signals yet —</div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
