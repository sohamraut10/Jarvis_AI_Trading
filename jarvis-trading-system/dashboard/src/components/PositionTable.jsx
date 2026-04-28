import { formatCurrency, pnlClass } from "../lib/utils";

const COL = "px-3 py-2 tabular-nums text-xs";
const HEADER = "px-3 py-1.5 text-[10px] text-gray-600 tracking-widest uppercase text-right";

export default function PositionTable({ snapshot }) {
  const positions = Object.values(snapshot?.positions ?? {});

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
              {positions.map((pos) => {
                const unrealised = (pos.ltp - pos.avg_price) * pos.quantity;
                const sideColour =
                  pos.quantity > 0 ? "text-green-400" : "text-red-400";
                return (
                  <tr
                    key={pos.symbol}
                    className="border-b border-gray-800/50 hover:bg-gray-800/30"
                  >
                    <td className={`${COL} text-left font-mono font-bold ${sideColour}`}>
                      {pos.symbol}
                    </td>
                    <td className={`${COL} text-right text-gray-300`}>
                      {pos.quantity}
                    </td>
                    <td className={`${COL} text-right text-gray-400`}>
                      {formatCurrency(pos.avg_price)}
                    </td>
                    <td className={`${COL} text-right text-gray-200`}>
                      {formatCurrency(pos.ltp)}
                    </td>
                    <td className={`${COL} text-right font-bold ${pnlClass(unrealised)}`}>
                      {formatCurrency(unrealised, true)}
                    </td>
                    <td className={`${COL} text-right ${pnlClass(pos.realised_pnl)}`}>
                      {formatCurrency(pos.realised_pnl, true)}
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
