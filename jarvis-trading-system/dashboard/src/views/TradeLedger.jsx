import { useEffect, useState } from "react";
import TradeCard from "../components/TradeCard";
import { formatCurrency, pnlClass } from "../lib/utils";

const SIDES = ["ALL", "BUY", "SELL"];

export default function TradeLedger() {
  const [trades, setTrades]     = useState([]);
  const [openPos, setOpenPos]   = useState([]);
  const [loading, setLoading]   = useState(true);
  const [filter, setFilter]     = useState("ALL");
  const [selected, setSelected] = useState(null);

  const load = () => {
    fetch("/api/trades")
      .then((r) => r.json())
      .then((d) => {
        setTrades(Array.isArray(d.trades) ? [...d.trades].reverse() : []);
        setOpenPos(Array.isArray(d.open_positions) ? d.open_positions : []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);
  // refresh every 10s
  useEffect(() => { const id = setInterval(load, 10_000); return () => clearInterval(id); }, []);

  const visible = trades.filter((t) => filter === "ALL" || t.side === filter);

  const TH = "px-3 py-2 text-[10px] text-gray-600 tracking-widest uppercase text-right font-normal";
  const TD = "px-3 py-2.5 text-xs tabular-nums";

  return (
    <div className="p-4 flex flex-col gap-4">

      {/* Open positions */}
      {openPos.length > 0 && (
        <div className="bg-gray-900 border border-cyan-900/40 rounded overflow-hidden">
          <div className="px-4 py-2.5 border-b border-gray-800 text-[10px] text-cyan-500 tracking-widest uppercase">
            Open Positions ({openPos.length})
          </div>
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-gray-800">
                  {["Symbol","Qty","Avg Entry","LTP","Unrealised P&L","Realised P&L"].map((h) => (
                    <th key={h} className={TH + " first:text-left"}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {openPos.map((p, i) => (
                  <tr key={i} className="border-b border-gray-800/40">
                    <td className={TD + " text-left font-bold text-cyan-300"}>{p.symbol}</td>
                    <td className={TD + " text-right " + (p.qty > 0 ? "text-green-400" : "text-red-400")}>
                      {p.qty > 0 ? "+" : ""}{p.qty}
                    </td>
                    <td className={TD + " text-right text-gray-400"}>₹{p.avg_price?.toFixed(2)}</td>
                    <td className={TD + " text-right text-gray-300"}>₹{p.ltp?.toFixed(2)}</td>
                    <td className={TD + " text-right font-bold " + pnlClass(p.unrealized)}>
                      {formatCurrency(p.unrealized, true)}
                    </td>
                    <td className={TD + " text-right font-bold " + pnlClass(p.realized)}>
                      {formatCurrency(p.realized, true)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Trade fills */}
      <div className="bg-gray-900 border border-gray-800 rounded overflow-hidden">
        <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-800">
          <span className="text-[10px] text-gray-500 tracking-widest uppercase">Trade Fills</span>
          <div className="flex gap-1 ml-auto">
            {SIDES.map((s) => (
              <button
                key={s}
                onClick={() => setFilter(s)}
                className={[
                  "px-2.5 py-1 text-[10px] font-bold rounded border tracking-widest transition-colors",
                  filter === s
                    ? "border-cyan-600 text-cyan-400 bg-cyan-900/30"
                    : "border-gray-700 text-gray-600 hover:text-gray-400",
                ].join(" ")}
              >
                {s}
              </button>
            ))}
          </div>
          <button
            onClick={load}
            className="text-[10px] text-gray-600 hover:text-gray-400 border border-gray-800 rounded px-2 py-1"
          >
            ↺
          </button>
          <span className="text-[10px] text-gray-700">{visible.length} fills</span>
        </div>

        {loading ? (
          <div className="text-center text-gray-700 text-xs py-12">Loading…</div>
        ) : visible.length === 0 ? (
          <div className="text-center text-gray-700 text-xs py-12">
            — no fills yet —<br />
            <span className="text-gray-800 text-[10px]">Orders appear here once strategies generate signals</span>
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-gray-800">
                  <th className={TH + " text-left"}>Time</th>
                  <th className={TH + " text-left"}>Symbol</th>
                  <th className={TH}>Side</th>
                  <th className={TH}>Qty</th>
                  <th className={TH}>Fill Price</th>
                  <th className={TH + " text-left"}>Strategy</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((t, i) => (
                  <tr
                    key={i}
                    onClick={() => setSelected(t)}
                    className="border-b border-gray-800/40 hover:bg-gray-800/40 cursor-pointer"
                  >
                    <td className={TD + " text-left text-gray-600 font-mono text-[10px]"}>
                      {t.timestamp ? new Date(t.timestamp).toLocaleTimeString("en-IN", { hour12: false }) : "—"}
                    </td>
                    <td className={TD + " text-left font-bold text-gray-200"}>{t.symbol}</td>
                    <td className={TD + " text-center"}>
                      <span className={t.side === "BUY" ? "text-green-400" : "text-red-400"}>{t.side}</span>
                    </td>
                    <td className={TD + " text-right text-gray-400"}>{t.qty}</td>
                    <td className={TD + " text-right text-gray-300"}>₹{t.entry_price?.toFixed(2)}</td>
                    <td className={TD + " text-left text-gray-500 text-[10px] font-mono"}>{t.strategy || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {selected && <TradeCard trade={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
