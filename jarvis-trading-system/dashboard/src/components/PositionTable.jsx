import { formatCurrency, pnlClass } from "../lib/utils";

const COL = "px-3 py-2 tabular-nums text-xs";
const HEADER = "px-3 py-1.5 text-[10px] text-gray-600 tracking-widest uppercase text-right";

export default function PositionTable({ snapshot }) {
  const openPositions = snapshot?.broker?.open_positions ?? {};
  const ltpMap        = snapshot?.ltp ?? {};
  const positions     = Object.entries(openPositions);

  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-4">
      <div className="text-[10px] text-gray-500 tracking-widest uppercase mb-3">
        Open Positions
      </div>

      {positions.length === 0 ? (
        <div className="text-xs text-gray-700 py-4 text-center">— no open positions —</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full border-collapse">
            <thead>
              <tr className="border-b border-gray-800">
                <th className={`${HEADER} text-left`}>Symbol</th>
                <th className={HEADER}>Qty</th>
                <th className={HEADER}>Avg</th>
                <th className={HEADER}>LTP</th>
                <th className={HEADER}>Unrealised</th>
                <th className={HEADER}>Realised</th>
              </tr>
            </thead>
            <tbody>
              {positions.map(([symbol, pos]) => {
                const ltp        = ltpMap[symbol] ?? pos.avg_price;
                const sideColour = pos.qty > 0 ? "text-green-400" : "text-red-400";
                const isForex    = !symbol.endsWith("INR") && symbol.length === 6;
                const fmt        = (v) => isForex
                  ? Number(v).toFixed(5)
                  : formatCurrency(v);
                return (
                  <tr
                    key={symbol}
                    className="border-b border-gray-800/50 hover:bg-gray-800/30"
                  >
                    <td className={`${COL} text-left font-mono font-bold ${sideColour}`}>
                      {symbol}
                    </td>
                    <td className={`${COL} text-right text-gray-300`}>
                      {pos.qty}
                    </td>
                    <td className={`${COL} text-right text-gray-400`}>
                      {fmt(pos.avg_price)}
                    </td>
                    <td className={`${COL} text-right text-gray-200`}>
                      {fmt(ltp)}
                    </td>
                    <td className={`${COL} text-right font-bold ${pnlClass(pos.unrealized_pnl)}`}>
                      {formatCurrency(pos.unrealized_pnl, true)}
                    </td>
                    <td className={`${COL} text-right ${pnlClass(pos.realized_pnl)}`}>
                      {formatCurrency(pos.realized_pnl, true)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
