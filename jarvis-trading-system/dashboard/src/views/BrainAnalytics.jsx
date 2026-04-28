import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatCurrency, pnlClass } from "../lib/utils";

const REGIMES = ["TRENDING_UP", "TRENDING_DOWN", "SIDEWAYS", "HIGH_VOL"];
const REGIME_COLOUR = {
  TRENDING_UP:   "text-green-400",
  TRENDING_DOWN: "text-red-400",
  SIDEWAYS:      "text-yellow-400",
  HIGH_VOL:      "text-orange-400",
};

function BrainVersionList({ versions }) {
  if (!versions?.length) {
    return <div className="text-[11px] text-gray-700 py-4 text-center">— no versions saved yet —</div>;
  }
  return (
    <div className="space-y-1">
      {versions.slice().reverse().map((v, i) => (
        <div key={v.name} className="flex items-center gap-3 py-1.5 border-b border-gray-800/40">
          <span className={`text-[9px] font-bold px-1.5 py-0.5 rounded ${i === 0 ? "bg-cyan-900/40 text-cyan-400 border border-cyan-800" : "text-gray-600"}`}>
            {i === 0 ? "CURRENT" : v.name.slice(0, 14)}
          </span>
          <span className="text-[10px] font-mono text-gray-500 flex-1">{v.name}</span>
          {v.sharpe != null && (
            <span className={`text-[10px] font-mono tabular-nums ${v.sharpe > 0 ? "text-green-400" : "text-red-400"}`}>
              SR {v.sharpe.toFixed(2)}
            </span>
          )}
        </div>
      ))}
    </div>
  );
}

function ConfusionMatrix({ matrix }) {
  return (
    <div className="overflow-x-auto">
      <table className="text-[10px] border-collapse mx-auto">
        <thead>
          <tr>
            <th className="p-1 text-gray-700 text-[9px]">Pred →</th>
            {REGIMES.map((r) => (
              <th key={r} className="p-1 text-gray-600 text-[9px] w-16 text-center">
                {r.replace("_", " ").slice(0, 6)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {REGIMES.map((actual) => (
            <tr key={actual}>
              <td className="pr-2 text-gray-600 text-[9px] whitespace-nowrap">
                {actual.replace("_", " ").slice(0, 6)}
              </td>
              {REGIMES.map((pred) => {
                const val = matrix?.[actual]?.[pred] ?? 0;
                const max = 20;
                const intensity = Math.min(val / max, 1);
                return (
                  <td
                    key={pred}
                    className="p-1 text-center font-mono border border-gray-800"
                    style={{
                      backgroundColor: `rgba(34,211,238,${intensity * 0.5})`,
                      color: intensity > 0.3 ? "#e2e8f0" : "#4b5563",
                    }}
                  >
                    {val}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function BrainAnalytics({ snapshot, pnlHistory }) {
  const [equity, setEquity]   = useState([]);
  const [intents, setIntents] = useState([]);
  const [matrix]              = useState(null); // populated when backend exposes it

  useEffect(() => {
    fetch("/api/equity").then((r) => r.json()).then((d) => setEquity(d.curve ?? [])).catch(() => {});
    fetch("/api/intent").then((r) => r.json()).then((d) => setIntents(d.events ?? [])).catch(() => {});
  }, []);

  const brainEvents = intents.filter((e) =>
    ["BRAIN_RETRAIN", "REGIME_CHANGE", "STRATEGY_ROTATION"].includes(e.event_type)
  );

  const equityData = equity.map((e) => ({ date: e.date, equity: e.equity }));

  return (
    <div className="p-4 grid grid-cols-1 lg:grid-cols-2 gap-4">
      {/* Equity curve */}
      <div className="bg-gray-900 border border-gray-800 rounded p-4 lg:col-span-2">
        <div className="text-[10px] text-gray-500 tracking-widest uppercase mb-3">Equity Curve</div>
        {equityData.length > 1 ? (
          <ResponsiveContainer width="100%" height={160}>
            <LineChart data={equityData} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#1f2937" vertical={false} />
              <XAxis dataKey="date" tick={{ fill: "#374151", fontSize: 9 }} tickLine={false} axisLine={false} />
              <YAxis tick={{ fill: "#374151", fontSize: 9 }} width={56}
                tickFormatter={(v) => `₹${(v / 1000).toFixed(0)}k`} tickLine={false} axisLine={false} />
              <Tooltip
                contentStyle={{ background: "#111827", border: "1px solid #374151", fontSize: 11 }}
                formatter={(v) => [formatCurrency(v), "Equity"]}
              />
              <Line type="monotone" dataKey="equity" stroke="#22d3ee" strokeWidth={2}
                dot={false} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        ) : (
          <div className="text-center text-gray-700 text-xs py-12">— equity curve builds after first trading day —</div>
        )}
      </div>

      {/* Brain versions */}
      <div className="bg-gray-900 border border-gray-800 rounded p-4">
        <div className="text-[10px] text-gray-500 tracking-widest uppercase mb-3">Brain Versions</div>
        <BrainVersionList versions={snapshot?.brain_versions ?? []} />
      </div>

      {/* Regime confusion matrix */}
      <div className="bg-gray-900 border border-gray-800 rounded p-4">
        <div className="text-[10px] text-gray-500 tracking-widest uppercase mb-3">
          Regime Confusion Matrix
        </div>
        <ConfusionMatrix matrix={matrix} />
        {!matrix && (
          <div className="text-center text-gray-700 text-[10px] mt-2">populated after HMM re-labels</div>
        )}
      </div>

      {/* Brain intent log */}
      <div className="bg-gray-900 border border-gray-800 rounded p-4 lg:col-span-2">
        <div className="text-[10px] text-gray-500 tracking-widest uppercase mb-3">Brain Event Log</div>
        {brainEvents.length === 0 ? (
          <div className="text-center text-gray-700 text-xs py-4">— no brain events yet —</div>
        ) : (
          <div className="space-y-1 max-h-48 overflow-y-auto font-mono text-[10px]">
            {brainEvents.slice().reverse().map((e, i) => (
              <div key={i} className="flex gap-3 py-1 border-b border-gray-800/30">
                <span className="text-gray-700 shrink-0 w-36 truncate">{e.ts ?? e.timestamp}</span>
                <span className={`shrink-0 font-bold ${REGIME_COLOUR[e.event_type] ?? "text-indigo-400"}`}>
                  🧠 {e.event_type}
                </span>
                <span className="text-gray-500 truncate flex-1">{e.why ?? e.details ?? ""}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
