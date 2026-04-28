export default function DrawdownBar({ dailyPnl, killThreshold }) {
  const drawdown = Math.max(-dailyPnl, 0);
  const pct = killThreshold > 0 ? Math.min((drawdown / killThreshold) * 100, 100) : 0;

  const colour =
    pct >= 80
      ? "bg-red-500"
      : pct >= 60
      ? "bg-yellow-500"
      : "bg-green-500";

  const label =
    pct >= 80 ? "text-red-400" : pct >= 60 ? "text-yellow-400" : "text-gray-500";

  return (
    <div className="flex items-center gap-2 px-4 py-1 bg-gray-950 border-b border-gray-800 shrink-0">
      <span className="text-[9px] text-gray-700 tracking-widest shrink-0 uppercase">
        Drawdown
      </span>
      <div className="flex-1 h-1 bg-gray-800 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${colour} ${pct >= 80 ? "animate-pulse" : ""}`}
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className={`text-[9px] tabular-nums shrink-0 font-mono ${label}`}>
        {pct.toFixed(0)}%
      </span>
      {pct >= 80 && (
        <span className="text-[9px] text-red-400 font-bold animate-pulse shrink-0">
          ⚠ NEAR KILL
        </span>
      )}
    </div>
  );
}
