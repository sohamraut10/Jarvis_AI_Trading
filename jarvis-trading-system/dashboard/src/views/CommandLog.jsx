import { useCallback, useEffect, useRef, useState } from "react";

const EVENT_TYPES = {
  REGIME_CHANGE:      { emoji: "🔵", cat: "REGIME",   colour: "text-blue-400",   bg: "bg-blue-950/20" },
  STRATEGY_ROTATION:  { emoji: "🟡", cat: "ROTATION", colour: "text-yellow-400", bg: "bg-yellow-950/20" },
  SIGNAL_GENERATED:   { emoji: "🟢", cat: "ENTRY",    colour: "text-green-400",  bg: "" },
  ORDER_PLACED:       { emoji: "🟢", cat: "ENTRY",    colour: "text-green-400",  bg: "" },
  TRADE_CLOSED_WIN:   { emoji: "✅", cat: "EXIT",     colour: "text-green-300",  bg: "bg-green-950/20" },
  TRADE_CLOSED_LOSS:  { emoji: "❌", cat: "EXIT",     colour: "text-red-400",    bg: "bg-red-950/10" },
  POSITION_SIZED:     { emoji: "📐", cat: "SIZING",   colour: "text-purple-400", bg: "" },
  BRAIN_RETRAIN:      { emoji: "🧠", cat: "BRAIN",    colour: "text-indigo-400", bg: "bg-indigo-950/20" },
  APPROVAL_REQUESTED: { emoji: "⚡", cat: "APPROVAL", colour: "text-amber-400",  bg: "bg-amber-950/20" },
  KILL_SWITCH:        { emoji: "🚨", cat: "RISK",     colour: "text-red-300",    bg: "bg-red-950/40 animate-pulse" },
  RISK_BREACH:        { emoji: "🚨", cat: "RISK",     colour: "text-red-300",    bg: "bg-red-950/30" },
};

const ALL_CATS = [...new Set(Object.values(EVENT_TYPES).map((e) => e.cat))];
const CAT_COLOUR = {
  REGIME: "text-blue-400 border-blue-800", ROTATION: "text-yellow-400 border-yellow-800",
  ENTRY: "text-green-400 border-green-800", EXIT: "text-green-400 border-green-800",
  SIZING: "text-purple-400 border-purple-800", BRAIN: "text-indigo-400 border-indigo-800",
  APPROVAL: "text-amber-400 border-amber-800", RISK: "text-red-400 border-red-800",
};

function style(event_type) {
  return EVENT_TYPES[event_type] ?? { emoji: "·", cat: "OTHER", colour: "text-gray-500", bg: "" };
}

export default function CommandLog() {
  const [events, setEvents]   = useState([]);
  const [active, setActive]   = useState(new Set(ALL_CATS));
  const [loading, setLoading] = useState(true);
  const bottomRef = useRef(null);

  const load = useCallback(() => {
    fetch("/api/intent?n=200")
      .then((r) => r.json())
      .then((d) => { setEvents(d.events ?? []); setLoading(false); })
      .catch(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 2000);
    return () => clearInterval(t);
  }, [load]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length]);

  const toggleCat = (cat) =>
    setActive((s) => {
      const n = new Set(s);
      n.has(cat) ? n.delete(cat) : n.add(cat);
      return n;
    });

  const visible = events.filter((e) => active.has(style(e.event_type).cat));

  return (
    <div className="p-4 flex flex-col h-full gap-3">
      {/* filter bar */}
      <div className="bg-gray-900 border border-gray-800 rounded px-3 py-2 flex items-center gap-2 flex-wrap shrink-0">
        <span className="text-[10px] text-gray-600 tracking-widest uppercase mr-1">Filter</span>
        {ALL_CATS.map((cat) => (
          <button
            key={cat}
            onClick={() => toggleCat(cat)}
            className={[
              "px-2 py-0.5 text-[9px] font-bold rounded border tracking-widest transition-colors",
              active.has(cat)
                ? CAT_COLOUR[cat] + " bg-opacity-10"
                : "border-gray-800 text-gray-700",
            ].join(" ")}
          >
            {cat}
          </button>
        ))}
        <span className="ml-auto text-[9px] text-gray-700">{visible.length} events</span>
      </div>

      {/* terminal */}
      <div className="bg-gray-950 border border-gray-800 rounded p-3 flex-1 overflow-y-auto font-mono text-[10px] min-h-0">
        {loading && <div className="text-gray-700 py-4 text-center">Loading…</div>}
        {!loading && visible.length === 0 && (
          <div className="text-gray-700 py-4 text-center">— no events —</div>
        )}
        {visible.map((e, i) => {
          const s = style(e.event_type);
          return (
            <div key={i} className={`flex gap-2 py-0.5 px-1 rounded ${s.bg}`}>
              <span className="text-gray-700 shrink-0 w-20 truncate">{e.ts?.slice(11, 19) ?? ""}</span>
              <span className="shrink-0">{s.emoji}</span>
              <span className={`shrink-0 w-24 font-bold ${s.colour}`}>{e.event_type}</span>
              <span className="text-gray-500 truncate flex-1">{e.why ?? e.details ?? ""}</span>
              {e.strategy && <span className="text-gray-700 shrink-0">[{e.strategy}]</span>}
            </div>
          );
        })}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
