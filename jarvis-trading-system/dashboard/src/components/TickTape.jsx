import { useEffect, useRef, useState } from "react";

const MAX_ITEMS = 40;

function TapeItem({ sym, price, dir, isForex }) {
  const priceStr = isForex ? Number(price).toFixed(4) : `₹${Number(price).toFixed(2)}`;
  const arrow    = dir === "up" ? "▲" : dir === "down" ? "▼" : "·";
  const color    = dir === "up" ? "text-green-400" : dir === "down" ? "text-red-400" : "text-gray-600";
  return (
    <span className="inline-flex items-center gap-1 px-2 border-r border-gray-800 shrink-0">
      <span className="text-gray-400 font-bold text-[10px]">{sym}</span>
      <span className={`text-[10px] font-mono tabular-nums ${color}`}>{priceStr}</span>
      <span className={`text-[9px] ${color}`}>{arrow}</span>
    </span>
  );
}

export default function TickTape({ snapshot }) {
  const [tape, setTape] = useState([]);
  const prevLtp = useRef({});
  const tapePaused = useRef(false);

  useEffect(() => {
    const ltp = snapshot?.ltp ?? {};
    const entries = Object.entries(ltp);
    if (!entries.length) return;

    const newItems = [];
    for (const [sym, price] of entries) {
      const prev = prevLtp.current[sym];
      const dir  = prev == null ? null : price > prev ? "up" : price < prev ? "down" : null;
      if (dir) {
        const isForex = !sym.endsWith("INR") && (sym.length === 6 || sym.includes("USD") || sym.includes("EUR") || sym.includes("GBP"));
        newItems.push({ sym, price, dir, isForex, key: `${sym}-${Date.now()}` });
      }
      prevLtp.current[sym] = price;
    }

    if (!newItems.length) return;
    setTape((prev) => {
      const next = [...prev, ...newItems];
      return next.length > MAX_ITEMS ? next.slice(-MAX_ITEMS) : next;
    });
  }, [snapshot?.ltp]);

  if (!tape.length) return (
    <div className="h-7 border-t border-gray-800 bg-gray-950 flex items-center px-3 shrink-0">
      <span className="text-[9px] text-gray-700 animate-pulse tracking-widest">AWAITING PRICE FEED…</span>
    </div>
  );

  return (
    <div
      className="h-7 border-t border-gray-800 bg-gray-950 flex items-center overflow-hidden shrink-0 relative"
      onMouseEnter={() => { tapePaused.current = true; }}
      onMouseLeave={() => { tapePaused.current = false; }}
    >
      <div
        className="flex items-center"
        style={{ animation: "ticktape 30s linear infinite", whiteSpace: "nowrap" }}
      >
        {tape.map((item) => (
          <TapeItem key={item.key} {...item} />
        ))}
        {/* duplicate for seamless loop */}
        {tape.map((item) => (
          <TapeItem key={`dup-${item.key}`} {...item} />
        ))}
      </div>
    </div>
  );
}
