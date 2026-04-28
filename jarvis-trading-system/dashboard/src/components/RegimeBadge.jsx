const STYLES = {
  TRENDING_UP: {
    wrap: "border-green-700 bg-green-950/60",
    text: "text-green-400",
    dot: "bg-green-400",
  },
  TRENDING_DOWN: {
    wrap: "border-red-700 bg-red-950/60",
    text: "text-red-400",
    dot: "bg-red-500",
  },
  SIDEWAYS: {
    wrap: "border-yellow-700 bg-yellow-950/60",
    text: "text-yellow-400",
    dot: "bg-yellow-400",
  },
  HIGH_VOL: {
    wrap: "border-orange-600 bg-orange-950/60",
    text: "text-orange-400",
    dot: "bg-orange-400",
  },
  UNKNOWN: {
    wrap: "border-gray-700 bg-gray-900/60",
    text: "text-gray-500",
    dot: "bg-gray-600",
  },
};

export default function RegimeBadge({ regime }) {
  const s = STYLES[regime] ?? STYLES.UNKNOWN;
  return (
    <div className={`flex items-center gap-2 px-3 py-1.5 rounded border ${s.wrap}`}>
      <span className={`w-2 h-2 rounded-full animate-pulse ${s.dot}`} />
      <span className={`text-xs font-bold tracking-widest ${s.text}`}>
        {regime ?? "UNKNOWN"}
      </span>
    </div>
  );
}
