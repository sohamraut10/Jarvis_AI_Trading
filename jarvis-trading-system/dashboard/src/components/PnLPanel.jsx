import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatCurrency, pnlClass } from "../lib/utils";

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;
  const val = payload[0].value;
  return (
    <div className="bg-gray-900 border border-gray-700 px-3 py-2 text-xs rounded">
      <div className="text-gray-500 mb-1">{label}</div>
      <div className={`font-bold tabular-nums ${pnlClass(val)}`}>
        {formatCurrency(val, true)}
      </div>
    </div>
  );
}

export default function PnLPanel({ pnlHistory, snapshot }) {
  const latest = pnlHistory[pnlHistory.length - 1];
  const pnl = latest?.pnl ?? 0;
  const positive = pnl >= 0;
  const colour = positive ? "#4ade80" : "#f87171";
  const killActive = snapshot?.broker?.kill_switch_active ?? false;

  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-4">
      {/* Header row */}
      <div className="flex items-baseline gap-3 mb-3">
        <span className="text-[10px] text-gray-500 tracking-widest uppercase">
          Session P&L
        </span>
        <span className={`text-2xl font-bold tabular-nums ${pnlClass(pnl)}`}>
          {formatCurrency(pnl, true)}
        </span>

        {killActive && (
          <span className="ml-2 text-xs font-bold text-red-400 animate-pulse tracking-widest">
            ⚠ KILL-SWITCH ACTIVE
          </span>
        )}

        <span className="ml-auto text-[10px] text-gray-700">
          {pnlHistory.length} pts
        </span>
      </div>

      {/* Chart */}
      <ResponsiveContainer width="100%" height={160}>
        <AreaChart
          data={pnlHistory}
          margin={{ top: 4, right: 4, left: 0, bottom: 0 }}
        >
          <defs>
            <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={colour} stopOpacity={0.35} />
              <stop offset="95%" stopColor={colour} stopOpacity={0.0} />
            </linearGradient>
          </defs>

          <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />

          <XAxis
            dataKey="t"
            tick={{ fill: "#374151", fontSize: 9 }}
            interval="preserveStartEnd"
            tickLine={false}
            axisLine={false}
          />
          <YAxis
            tick={{ fill: "#374151", fontSize: 9 }}
            width={56}
            tickFormatter={(v) => `₹${v}`}
            tickLine={false}
            axisLine={false}
          />

          <ReferenceLine y={0} stroke="#374151" strokeDasharray="4 4" />

          <Tooltip content={<CustomTooltip />} cursor={{ stroke: "#4b5563" }} />

          <Area
            type="monotone"
            dataKey="pnl"
            stroke={colour}
            strokeWidth={2}
            fill="url(#pnlGrad)"
            dot={false}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
