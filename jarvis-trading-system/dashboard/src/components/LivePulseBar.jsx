import { useEffect, useRef, useState } from "react";

function Metric({ label, value, unit = "", pulse = false, color = "text-cyan-400" }) {
  const [flash, setFlash] = useState(false);
  const prev = useRef(value);

  useEffect(() => {
    if (prev.current !== value) {
      setFlash(true);
      const t = setTimeout(() => setFlash(false), 600);
      prev.current = value;
      return () => clearTimeout(t);
    }
  }, [value]);

  return (
    <div className={`flex flex-col items-center px-3 py-1.5 border-r border-gray-800 last:border-0 transition-colors ${flash ? "bg-cyan-950/30" : ""}`}>
      <span className="text-[8px] text-gray-600 tracking-widest uppercase">{label}</span>
      <span className={`text-sm font-bold font-mono tabular-nums ${color} ${pulse ? "animate-pulse" : ""}`}>
        {value}<span className="text-[10px] text-gray-600 ml-0.5">{unit}</span>
      </span>
    </div>
  );
}

function UptimeDisplay({ secs }) {
  if (secs == null) return <span className="text-gray-700">—</span>;
  const h = Math.floor(secs / 3600);
  const m = Math.floor((secs % 3600) / 60);
  const s = Math.floor(secs % 60);
  if (h > 0) return <span>{h}h {String(m).padStart(2, "0")}m</span>;
  return <span>{String(m).padStart(2, "0")}:{String(s).padStart(2, "0")}</span>;
}

export default function LivePulseBar({ snapshot, connected, activityCount }) {
  const sys    = snapshot?.system ?? {};
  const broker = snapshot?.broker ?? {};
  const scanner = snapshot?.scanner ?? {};
  const liveCount = Object.values(scanner).filter((d) => d.status === "live").length;

  const tickRate = sys.tick_rate ?? 0;
  const isFeeding = tickRate > 0;

  return (
    <div className="flex items-stretch border-b border-gray-800 bg-gray-950/80 overflow-x-auto shrink-0">
      {/* Feed indicator */}
      <div className="flex items-center gap-2 px-4 border-r border-gray-800">
        <span className={`w-2 h-2 rounded-full shrink-0 ${isFeeding ? "bg-green-400 animate-pulse" : connected ? "bg-yellow-500 animate-pulse" : "bg-red-600"}`} />
        <span className={`text-[9px] font-bold tracking-widest ${isFeeding ? "text-green-400" : "text-gray-600"}`}>
          {isFeeding ? "FEED LIVE" : connected ? "WAITING" : "OFFLINE"}
        </span>
      </div>

      <Metric
        label="Ticks/s"
        value={tickRate.toFixed(1)}
        color={tickRate > 1 ? "text-green-400" : tickRate > 0 ? "text-yellow-400" : "text-gray-700"}
      />
      <Metric label="Total Ticks" value={(sys.tick_total ?? 0).toLocaleString()} color="text-gray-300" />
      <Metric label="Signals Today" value={sys.signal_total ?? 0} color="text-cyan-400" />
      <Metric label="Instruments" value={liveCount} unit=" live" color={liveCount > 0 ? "text-green-400" : "text-gray-600"} />
      <Metric
        label="Open Trades"
        value={Object.keys(broker.open_positions ?? {}).length}
        color="text-yellow-400"
      />
      <Metric
        label="Day P&L"
        value={`${(broker.daily_pnl ?? 0) >= 0 ? "+" : ""}₹${Math.abs(broker.daily_pnl ?? 0).toFixed(0)}`}
        color={(broker.daily_pnl ?? 0) >= 0 ? "text-green-400" : "text-red-400"}
        pulse={(broker.daily_pnl ?? 0) !== 0}
      />
      <Metric label="Events" value={activityCount ?? 0} color="text-purple-400" />
      <div className="flex flex-col items-center justify-center px-3 py-1.5">
        <span className="text-[8px] text-gray-600 tracking-widest uppercase">Uptime</span>
        <span className="text-sm font-bold font-mono text-gray-400">
          <UptimeDisplay secs={sys.uptime_secs} />
        </span>
      </div>
    </div>
  );
}
