import { useEffect, useRef, useState } from "react";
import { formatCurrency } from "../lib/utils";

export default function ApprovalBanner({ pending, onApprove, onReject, onSnooze, timeoutSecs }) {
  if (!pending.length) return null;
  const item = pending[0];

  return (
    <div className="bg-amber-950/70 border-y border-amber-700 px-4 py-2 flex items-center gap-4 shrink-0">
      <span className="text-amber-400 font-bold text-xs tracking-widest shrink-0">⚡ APPROVAL</span>

      <div className="flex-1 min-w-0">
        <span className="text-amber-200 text-xs font-mono">{item.description ?? "Pending decision"}</span>
        {item.expected_impact != null && (
          <span
            className={`ml-3 text-xs font-bold tabular-nums ${
              item.expected_impact >= 0 ? "text-green-400" : "text-red-400"
            }`}
          >
            {item.expected_impact >= 0 ? "+" : ""}
            {formatCurrency(item.expected_impact, true)}
          </span>
        )}
      </div>

      <Countdown
        key={item.id}
        secs={timeoutSecs}
        onExpire={() => onApprove(item.id)}
      />

      <div className="flex gap-2 shrink-0">
        <Btn colour="green" onClick={() => onApprove(item.id)}>APPROVE</Btn>
        <Btn colour="gray"  onClick={() => onSnooze(item.id)}>SNOOZE</Btn>
        <Btn colour="red"   onClick={() => onReject(item.id)}>REJECT</Btn>
      </div>

      {pending.length > 1 && (
        <span className="text-[10px] text-amber-700 shrink-0">+{pending.length - 1} more</span>
      )}
    </div>
  );
}

function Countdown({ secs, onExpire }) {
  const [left, setLeft] = useState(secs);
  const ref = useRef(null);

  useEffect(() => {
    setLeft(secs);
    ref.current = setInterval(() => {
      setLeft((n) => {
        if (n <= 1) { clearInterval(ref.current); onExpire(); return 0; }
        return n - 1;
      });
    }, 1000);
    return () => clearInterval(ref.current);
  }, [secs, onExpire]);

  const pct = ((secs - left) / secs) * 100;
  return (
    <div className="flex items-center gap-1.5 shrink-0">
      <div className="w-16 h-1 bg-gray-800 rounded-full overflow-hidden">
        <div
          className="h-full bg-amber-500 transition-all duration-1000"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-[10px] tabular-nums text-amber-600 font-mono w-5">{left}s</span>
    </div>
  );
}

function Btn({ colour, onClick, children }) {
  const cls = {
    green: "border-green-700 text-green-400 hover:bg-green-900/40",
    red:   "border-red-800 text-red-400 hover:bg-red-900/40",
    gray:  "border-gray-700 text-gray-400 hover:bg-gray-800",
  }[colour];
  return (
    <button
      onClick={onClick}
      className={`px-2.5 py-1 text-[10px] font-bold tracking-widest border rounded transition-colors ${cls}`}
    >
      {children}
    </button>
  );
}
