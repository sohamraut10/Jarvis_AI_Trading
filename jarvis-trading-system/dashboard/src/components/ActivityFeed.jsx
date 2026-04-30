import { useEffect, useRef } from "react";

const TYPE_CFG = {
  signal_buy:     { icon: "▲", color: "text-green-400", bg: "bg-green-950/20",  border: "border-green-900/40"  },
  signal_sell:    { icon: "▼", color: "text-red-400",   bg: "bg-red-950/20",    border: "border-red-900/40"    },
  signal_exit:    { icon: "◆", color: "text-yellow-400",bg: "bg-yellow-950/20", border: "border-yellow-900/40" },
  regime_change:  { icon: "⟳", color: "text-purple-400",bg: "bg-purple-950/20", border: "border-purple-900/40" },
  trade_open:     { icon: "●", color: "text-cyan-400",  bg: "bg-cyan-950/20",   border: "border-cyan-900/40"   },
  trade_close:    { icon: "○", color: "text-blue-400",  bg: "bg-blue-950/20",   border: "border-blue-900/40"   },
  system:         { icon: "·", color: "text-gray-500",  bg: "",                 border: "border-gray-800/20"   },
  connected:      { icon: "◉", color: "text-green-500", bg: "bg-green-950/10",  border: "border-green-900/30"  },
  tick_milestone: { icon: "◈", color: "text-cyan-600",  bg: "",                 border: "border-gray-800/20"   },
};

function ActivityRow({ item }) {
  const cfg = TYPE_CFG[item.type] ?? TYPE_CFG.system;
  return (
    <div className={`flex items-start gap-2 px-2 py-1.5 rounded border mb-1 ${cfg.bg} ${cfg.border} text-[10px] font-mono`}>
      <span className={`shrink-0 mt-0.5 ${cfg.color} text-[9px]`}>{cfg.icon}</span>
      <div className="flex-1 min-w-0">
        <span className={`font-bold ${cfg.color}`}>{item.title}</span>
        {item.detail && <span className="text-gray-600 ml-1.5">{item.detail}</span>}
      </div>
      <span className="shrink-0 text-gray-700 text-[8px]">{item.time}</span>
    </div>
  );
}

export default function ActivityFeed({ activity = [] }) {
  const bottomRef = useRef(null);
  const containerRef = useRef(null);
  const atBottomRef = useRef(true);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const onScroll = () => {
      atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
    };
    el.addEventListener("scroll", onScroll);
    return () => el.removeEventListener("scroll", onScroll);
  }, []);

  useEffect(() => {
    if (atBottomRef.current) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [activity.length]);

  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-4 flex flex-col min-h-0">
      <div className="flex items-center justify-between mb-2 shrink-0">
        <span className="text-[10px] text-gray-500 tracking-widest uppercase">Live Activity</span>
        <span className="text-[9px] text-gray-700">{activity.length} events</span>
      </div>
      <div ref={containerRef} className="overflow-y-auto flex-1 max-h-72 min-h-0">
        {activity.length === 0 ? (
          <div className="text-xs text-gray-700 py-6 text-center animate-pulse">
            — waiting for activity —
          </div>
        ) : (
          activity.map((item, i) => <ActivityRow key={i} item={item} />)
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
