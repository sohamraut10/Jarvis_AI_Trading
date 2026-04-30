import { useEffect, useRef } from "react";
import { enumValue } from "../lib/utils";

const SIDE_STYLE = {
  BUY:  "text-green-400 border-green-800 bg-green-950/40",
  SELL: "text-red-400   border-red-800   bg-red-950/40",
  EXIT: "text-yellow-400 border-yellow-800 bg-yellow-950/40",
};

function SignalRow({ sig }) {
  const side  = enumValue(sig.side ?? "");
  const style = SIDE_STYLE[side] ?? "text-gray-400 border-gray-700 bg-gray-900";
  const timeStr = sig.ts
    ? new Date(sig.ts).toLocaleTimeString("en-IN", { hour12: false })
    : (sig.t ?? "");
  const price    = sig.entry ?? sig.price;
  const approved = sig.approved;

  return (
    <div className={`flex items-center gap-2 py-1.5 border-b border-gray-800/50 text-xs font-mono ${approved === false ? "opacity-50" : ""}`}>
      <span className="text-gray-600 shrink-0 w-16 truncate">{timeStr}</span>
      <span className={`shrink-0 px-1.5 py-0.5 rounded border text-[10px] font-bold tracking-widest ${style}`}>
        {side || "?"}
      </span>
      <span className="text-gray-300 shrink-0 truncate max-w-[80px]">{sig.symbol}</span>
      <span className="text-gray-500 shrink-0 truncate flex-1">{sig.strategy}</span>
      {price != null && (
        <span className="text-gray-400 shrink-0">
          {Number(price) < 100 ? Number(price).toFixed(5) : `₹${Number(price).toFixed(2)}`}
        </span>
      )}
      {approved === false && <span className="text-red-900 text-[9px] shrink-0">✗</span>}
      {approved === true  && <span className="text-green-900 text-[9px] shrink-0">✓</span>}
    </div>
  );
}

export default function SignalLog({ signals = [] }) {
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [signals.length]);

  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-4 flex flex-col min-h-0">
      <div className="flex items-center justify-between mb-2 shrink-0">
        <span className="text-[10px] text-gray-500 tracking-widest uppercase">Signal Log</span>
        <span className="text-[9px] text-gray-700">{signals.length} signals</span>
      </div>
      <div className="overflow-y-auto flex-1 max-h-48 pr-1">
        {signals.length === 0 ? (
          <div className="text-xs text-gray-700 py-4 text-center animate-pulse">
            — strategies hunting for entries —
          </div>
        ) : (
          signals.map((sig, i) => <SignalRow key={i} sig={sig} />)
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
