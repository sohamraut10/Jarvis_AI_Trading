import { formatCurrency, pnlClass } from "../lib/utils";

function Row({ label, value, valueClass = "text-gray-300" }) {
  return (
    <div className="flex justify-between items-baseline py-1 border-b border-gray-800/50">
      <span className="text-[10px] text-gray-600 tracking-widest uppercase">{label}</span>
      <span className={`text-xs font-mono tabular-nums ${valueClass}`}>{value ?? "—"}</span>
    </div>
  );
}

function Section({ title, children }) {
  return (
    <div className="mb-4">
      <div className="text-[9px] text-gray-700 tracking-[0.2em] uppercase mb-1">{title}</div>
      {children}
    </div>
  );
}

export default function TradeCard({ trade, onClose }) {
  if (!trade) return null;
  const net = (trade.pnl ?? 0) - (trade.brokerage ?? 0) - (trade.stt ?? 0);

  return (
    <div className="fixed inset-0 z-40 flex">
      {/* backdrop */}
      <div className="flex-1 bg-black/60" onClick={onClose} />

      {/* panel */}
      <div className="w-96 bg-gray-950 border-l border-gray-800 flex flex-col overflow-y-auto">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800 shrink-0">
          <div>
            <span className="font-bold text-gray-100 tracking-widest">{trade.symbol ?? "TRADE"}</span>
            <span
              className={`ml-2 text-[10px] font-bold px-1.5 py-0.5 rounded border ${
                trade.side === "BUY"
                  ? "text-green-400 border-green-800 bg-green-950/40"
                  : "text-red-400 border-red-800 bg-red-950/40"
              }`}
            >
              {trade.side}
            </span>
          </div>
          <button onClick={onClose} className="text-gray-600 hover:text-gray-300 text-lg leading-none">✕</button>
        </div>

        <div className="p-4 space-y-4 text-xs">
          {/* P&L block */}
          <Section title="Profit & Loss">
            <Row label="Gross P&L"     value={formatCurrency(trade.pnl, true)}        valueClass={pnlClass(trade.pnl)} />
            <Row label="Brokerage"     value={formatCurrency(-(trade.brokerage ?? 0), true)} valueClass="text-red-400" />
            <Row label="STT / Taxes"   value={formatCurrency(-(trade.stt ?? 0), true)}       valueClass="text-red-400" />
            <Row label="Net P&L"       value={formatCurrency(net, true)}               valueClass={`font-bold ${pnlClass(net)}`} />
          </Section>

          {/* Execution */}
          <Section title="Execution">
            <Row label="Entry price"   value={formatCurrency(trade.entry_price)} />
            <Row label="Exit price"    value={formatCurrency(trade.exit_price)} />
            <Row label="Qty"           value={trade.qty} />
            <Row label="Hard stop"     value={formatCurrency(trade.stop_level)} />
            <Row label="Duration"      value={trade.duration_secs != null ? `${Math.round(trade.duration_secs)}s` : null} />
          </Section>

          {/* Risk metrics */}
          <Section title="Risk Metrics">
            <Row label="MFE"           value={formatCurrency(trade.mfe, true)} valueClass="text-green-400" />
            <Row label="MAE"           value={formatCurrency(trade.mae, true)} valueClass="text-red-400" />
            <Row label="R/R achieved"  value={trade.rr_achieved != null ? `${trade.rr_achieved.toFixed(2)}R` : null} />
            <Row label="Kelly fraction" value={trade.kelly_fraction != null ? `${(trade.kelly_fraction * 100).toFixed(1)}%` : null} />
          </Section>

          {/* Regime context */}
          <Section title="Regime Context at Entry">
            <Row label="Regime"        value={trade.regime} />
            <Row label="Strategy"      value={trade.strategy} />
            <Row label="Signal group"  value={trade.signal_group ?? "—"} />
            <Row label="Sharpe rank"   value={trade.sharpe_rank != null ? `#${trade.sharpe_rank}` : null} />
          </Section>

          {/* JARVIS reasoning */}
          {trade.reasoning && (
            <Section title="JARVIS Reasoning">
              <p className="text-gray-500 leading-relaxed text-[10px] font-mono whitespace-pre-wrap">
                {trade.reasoning}
              </p>
            </Section>
          )}
        </div>
      </div>
    </div>
  );
}
