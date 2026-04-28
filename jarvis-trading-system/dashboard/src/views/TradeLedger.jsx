import { useEffect, useState } from "react";
import TradeCard from "../components/TradeCard";
import { formatCurrency, pnlClass } from "../lib/utils";

const SIDES = ["ALL", "BUY", "SELL"];

export default function TradeLedger() {
  const [trades, setTrades]   = useState([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter]   = useState("ALL");
  const [selected, setSelected] = useState(null);

  useEffect(() => {
    fetch("/api/pnl")
      .then((r) => r.json())
      .then((d) => {
        setTrades(Array.isArray(d.trades) ? d.trades : []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const visible = trades.filter((t) => filter === "ALL" || t.side === filter);

  const TH = "px-3 py-2 text-[10px] text-gray-600 tracking-widest uppercase text-right font-normal";
  const TD = "px-3 py-2.5 text-xs tabular-nums";

  return (
    <div className="p-4">
      <div className="bg-gray-900 border border-gray-800 rounded overflow-hidden">
        {/* filter bar */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-800">
          <span className="text-[10px] text-gray-500 tracking-widest uppercase">Trade Ledger</span>
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
          <span className="text-[10px] text-gray-700">{visible.length} trades</span>
        </div>

        {loading ? (
          <div className="text-center text-gray-700 text-xs py-12">Loading…</div>
        ) : visible.length === 0 ? (
          <div className="text-center text-gray-700 text-xs py-12">— no trades yet —</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse">
              <thead>
                <tr className="border-b border-gray-800">
                  <th className={`${TH} text-left`}>Time</th>
                  <th className={`${TH} text-left`}>Symbol</th>
                  <th className={TH}>Side</th>
                  <th className={TH}>Qty</th>
                  <th className={TH}>Entry</th>
                  <th className={TH}>Exit</th>
                  <th className={TH}>Regime</th>
                  <th className={TH}>Gross P&L</th>
                  <th className={TH}>Net P&L</th>
                </tr>
              </thead>
              <tbody>
                {visible.map((t, i) => {
                  const net = (t.pnl ?? 0) - (t.brokerage ?? 0) - (t.stt ?? 0);
                  return (
                    <tr
                      key={i}
                      onClick={() => setSelected(t)}
                      className="border-b border-gray-800/40 hover:bg-gray-800/40 cursor-pointer"
                    >
                      <td className={`${TD} text-left text-gray-600 font-mono text-[10px]`}>
                        {t.timestamp ? new Date(t.timestamp).toLocaleTimeString() : "—"}
                      </td>
                      <td className={`${TD} text-left font-bold text-gray-200`}>{t.symbol}</td>
                      <td className={`${TD} text-center`}>
                        <span className={t.side === "BUY" ? "text-green-400" : "text-red-400"}>{t.side}</span>
                      </td>
                      <td className={`${TD} text-right text-gray-400`}>{t.qty}</td>
                      <td className={`${TD} text-right text-gray-400`}>{formatCurrency(t.entry_price)}</td>
                      <td className={`${TD} text-right text-gray-400`}>{formatCurrency(t.exit_price)}</td>
                      <td className={`${TD} text-right text-gray-600 text-[10px]`}>{t.regime ?? "—"}</td>
                      <td className={`${TD} text-right font-bold ${pnlClass(t.pnl)}`}>
                        {formatCurrency(t.pnl, true)}
                      </td>
                      <td className={`${TD} text-right font-bold ${pnlClass(net)}`}>
                        {formatCurrency(net, true)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {selected && <TradeCard trade={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
