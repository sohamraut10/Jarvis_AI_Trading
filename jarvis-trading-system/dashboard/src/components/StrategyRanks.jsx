const BAR_MAX_W = 100; // percent

function AllocationBar({ name, allocation, sharpe }) {
  const pct = Math.round((allocation ?? 0) * 100);
  const barW = Math.min(pct, BAR_MAX_W);
  const sharpeColour =
    sharpe > 0.5 ? "text-green-400" : sharpe < 0 ? "text-red-400" : "text-yellow-400";

  return (
    <div className="flex items-center gap-2 py-1">
      <span className="text-[10px] font-mono text-gray-400 w-32 truncate shrink-0">
        {name}
      </span>
      <div className="flex-1 bg-gray-800 rounded-full h-1.5 overflow-hidden">
        <div
          className="h-full rounded-full bg-cyan-500 transition-all duration-500"
          style={{ width: `${barW}%` }}
        />
      </div>
      <span className="text-[10px] tabular-nums text-gray-400 w-8 text-right shrink-0">
        {pct}%
      </span>
      <span className={`text-[10px] tabular-nums w-12 text-right shrink-0 ${sharpeColour}`}>
        {sharpe != null ? sharpe.toFixed(2) : "—"}
      </span>
    </div>
  );
}

export default function StrategyRanks({ snapshot }) {
  const allocations = snapshot?.allocations ?? {};
  const sharpes = snapshot?.sharpes ?? {};

  const entries = Object.keys(allocations)
    .map((name) => ({
      name,
      allocation: allocations[name] ?? 0,
      sharpe: sharpes[name] ?? null,
    }))
    .sort((a, b) => b.allocation - a.allocation);

  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-4">
      <div className="flex items-baseline justify-between mb-3">
        <span className="text-[10px] text-gray-500 tracking-widest uppercase">
          Strategy Allocation
        </span>
        <span className="text-[10px] text-gray-700">sharpe →</span>
      </div>

      {entries.length === 0 ? (
        <div className="text-xs text-gray-700 py-2 text-center">— no data —</div>
      ) : (
        <div className="space-y-0.5">
          {entries.map((e) => (
            <AllocationBar
              key={e.name}
              name={e.name}
              allocation={e.allocation}
              sharpe={e.sharpe}
            />
          ))}
        </div>
      )}
    </div>
  );
}
