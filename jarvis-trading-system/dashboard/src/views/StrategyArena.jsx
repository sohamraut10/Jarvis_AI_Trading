import { useState } from "react";
import StrategyDeepDive from "../components/StrategyDeepDive";

const STRATEGY_DISPLAY = {
  ema_crossover: "EMA Crossover",
  supertrend:    "SuperTrend",
  orb_breakout:  "ORB Breakout",
  rsi_momentum:  "RSI Momentum",
  vwap_breakout: "VWAP Breakout",
};

export default function StrategyArena({ snapshot, signals }) {
  const [selected, setSelected] = useState(null);
  const allocations = snapshot?.allocations ?? {};
  const sharpes     = snapshot?.sharpes ?? {};

  const rows = Object.keys({ ...allocations, ...sharpes })
    .map((name, i) => ({
      rank: i + 1,
      name,
      display: STRATEGY_DISPLAY[name] ?? name,
      allocation: allocations[name] ?? 0,
      sharpe:     sharpes[name] ?? null,
      stats:      snapshot?.strategy_stats?.[name] ?? null,
    }))
    .sort((a, b) => b.allocation - a.allocation)
    .map((r, i) => ({ ...r, rank: i + 1 }));

  const TH = "px-3 py-2 text-[10px] text-gray-600 tracking-widest uppercase text-right font-normal";
  const TD = "px-3 py-2.5 text-xs tabular-nums text-right";

  return (
    <div className="p-4">
      <div className="bg-gray-900 border border-gray-800 rounded overflow-hidden">
        <div className="flex items-baseline gap-3 px-4 py-3 border-b border-gray-800">
          <span className="text-[10px] text-gray-500 tracking-widest uppercase">Strategy Arena</span>
          <span className="text-[10px] text-gray-700">click row for deep dive</span>
        </div>
        <table className="w-full border-collapse">
          <thead>
            <tr className="border-b border-gray-800">
              <th className={`${TH} text-left`}>#</th>
              <th className={`${TH} text-left`}>Strategy</th>
              <th className={TH}>Allocation</th>
              <th className={TH}>Sharpe</th>
              <th className={TH}>Win rate</th>
              <th className={TH}>Trades</th>
              <th className={TH}>Alloc bar</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 ? (
              <tr>
                <td colSpan={7} className="text-center text-gray-700 text-xs py-8">
                  — waiting for strategy data —
                </td>
              </tr>
            ) : rows.map((r) => {
              const sharpeColour =
                r.sharpe == null ? "text-gray-600"
                : r.sharpe > 0.5 ? "text-green-400"
                : r.sharpe < 0   ? "text-red-400"
                : "text-yellow-400";

              return (
                <tr
                  key={r.name}
                  onClick={() => setSelected(r)}
                  className="border-b border-gray-800/50 hover:bg-gray-800/40 cursor-pointer transition-colors"
                >
                  <td className={`${TD} text-left text-gray-600 font-mono`}>{r.rank}</td>
                  <td className={`${TD} text-left text-gray-200 font-bold`}>{r.display}</td>
                  <td className={`${TD} text-gray-300`}>{(r.allocation * 100).toFixed(1)}%</td>
                  <td className={`${TD} font-bold ${sharpeColour}`}>
                    {r.sharpe != null ? r.sharpe.toFixed(3) : "—"}
                  </td>
                  <td className={`${TD} text-gray-400`}>
                    {r.stats?.win_rate != null ? `${(r.stats.win_rate * 100).toFixed(0)}%` : "—"}
                  </td>
                  <td className={`${TD} text-gray-400`}>{r.stats?.total_trades ?? "—"}</td>
                  <td className="px-3 py-2.5">
                    <div className="w-full bg-gray-800 rounded-full h-1.5 overflow-hidden">
                      <div
                        className="h-full bg-cyan-500 rounded-full"
                        style={{ width: `${Math.min(r.allocation * 100, 100)}%` }}
                      />
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {selected && (
        <StrategyDeepDive
          strategy={selected.name}
          stats={selected.stats}
          sharpe={selected.sharpe}
          allocation={selected.allocation}
          signals={signals}
          onClose={() => setSelected(null)}
        />
      )}
    </div>
  );
}
