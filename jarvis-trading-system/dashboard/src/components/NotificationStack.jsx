const LEVEL_STYLE = {
  info:  "border-gray-700 bg-gray-900 text-gray-300",
  warn:  "border-yellow-700 bg-yellow-950/60 text-yellow-300",
  error: "border-red-700 bg-red-950/60 text-red-300",
};

const CAT_COLOUR = {
  REGIME:   "text-blue-400",
  ROTATION: "text-yellow-400",
  ENTRY:    "text-green-400",
  EXIT:     "text-green-400",
  SIZING:   "text-purple-400",
  BRAIN:    "text-indigo-400",
  APPROVAL: "text-amber-400",
  RISK:     "text-red-400",
};

export default function NotificationStack({ banners, onDismiss }) {
  if (!banners.length) return null;
  return (
    <div className="fixed top-24 right-4 z-50 flex flex-col gap-2 pointer-events-none">
      {banners.map((n) => (
        <div
          key={n.id}
          className={`pointer-events-auto flex items-start gap-2 px-3 py-2 rounded border text-xs max-w-xs shadow-lg ${LEVEL_STYLE[n.level] ?? LEVEL_STYLE.info}`}
        >
          <span className={`font-bold shrink-0 ${CAT_COLOUR[n.category] ?? "text-gray-400"}`}>
            {n.category}
          </span>
          <span className="flex-1">{n.message}</span>
          <button
            onClick={() => onDismiss(n.id)}
            className="shrink-0 text-gray-600 hover:text-gray-400 ml-1"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}
