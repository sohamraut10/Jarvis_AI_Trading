import { useState } from "react";

const SCORE_COLOR = (s) => {
  if (s >= 70) return "text-green-400";
  if (s >= 50) return "text-yellow-400";
  return "text-red-400";
};

const BAR_COLOR = (s) => {
  if (s >= 70) return "bg-green-500";
  if (s >= 50) return "bg-yellow-500";
  return "bg-red-500";
};

function ComponentBar({ label, value }) {
  const pct = Math.round(value * 100);
  return (
    <div className="flex items-center gap-1.5 py-0.5">
      <span className="text-[8px] text-gray-600 w-16 shrink-0 uppercase tracking-wide">{label}</span>
      <div className="flex-1 bg-gray-800 rounded-full h-1">
        <div
          className="h-1 rounded-full bg-cyan-700 transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="text-[8px] text-gray-600 font-mono w-6 text-right">{pct}</span>
    </div>
  );
}

function ScoreRow({ item, index, selected, onPinToggle }) {
  const [expanded, setExpanded] = useState(false);
  const isPinned = selected.includes(item.symbol);

  return (
    <div
      className={[
        "border-b border-gray-800/50 last:border-0",
        item.recommended ? "bg-cyan-950/10" : "",
      ].join(" ")}
    >
      <div
        className="flex items-center gap-2 py-1.5 px-1 cursor-pointer hover:bg-gray-800/30 rounded transition-colors"
        onClick={() => setExpanded((v) => !v)}
      >
        {/* Rank badge */}
        <div className="w-4 text-center shrink-0">
          {item.recommended ? (
            <span className="text-[8px] font-bold text-cyan-400">#{item.rank}</span>
          ) : (
            <span className="text-[9px] text-gray-700">#{item.rank}</span>
          )}
        </div>

        {/* Symbol */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1">
            <span className={`text-xs font-mono font-bold ${item.recommended ? "text-gray-200" : "text-gray-500"}`}>
              {item.symbol}
            </span>
            {item.recommended && (
              <span className="text-[7px] px-1 py-0.5 rounded bg-cyan-900/50 border border-cyan-800/60 text-cyan-500 font-bold tracking-widest">
                AI PICK
              </span>
            )}
          </div>
          {!expanded && (
            <div className="text-[8px] text-gray-700 truncate mt-0.5">{item.reasoning}</div>
          )}
        </div>

        {/* Score */}
        <div className="flex items-center gap-1.5 shrink-0">
          <div className="w-12 bg-gray-800 rounded-full h-1.5">
            <div
              className={`h-1.5 rounded-full ${BAR_COLOR(item.score)} transition-all duration-500`}
              style={{ width: `${item.score}%` }}
            />
          </div>
          <span className={`text-xs font-mono font-bold w-8 ${SCORE_COLOR(item.score)}`}>
            {item.score.toFixed(0)}
          </span>
        </div>

        {/* Pin button */}
        <button
          onClick={(e) => { e.stopPropagation(); onPinToggle(item.symbol); }}
          title={isPinned ? "Unpin from manual override" : "Pin to manual override"}
          className={[
            "w-5 h-5 flex items-center justify-center rounded text-[9px] transition-colors border shrink-0",
            isPinned
              ? "bg-cyan-900/60 border-cyan-700 text-cyan-400"
              : "border-gray-800 text-gray-700 hover:border-gray-600 hover:text-gray-500",
          ].join(" ")}
        >
          {isPinned ? "●" : "○"}
        </button>
      </div>

      {expanded && item.components && (
        <div className="px-3 pb-2 pt-1">
          <div className="text-[8px] text-gray-600 mb-1">{item.reasoning}</div>
          <ComponentBar label="Volatility"  value={item.components.volatility} />
          <ComponentBar label="Trend"       value={item.components.trend} />
          <ComponentBar label="Liquidity"   value={item.components.ticks} />
          <ComponentBar label="Sig.Conf"    value={item.components.signal_conf} />
          <ComponentBar label="Regime Fit"  value={item.components.regime_fit} />
        </div>
      )}
    </div>
  );
}

export default function IntelligencePanel({ snapshot }) {
  const intel       = snapshot?.intelligence ?? {};
  const scores      = intel.scores ?? [];
  const autoSelect  = intel.auto_select ?? true;
  const selected    = intel.selected_symbols ?? [];
  const [manualPins, setManualPins] = useState([]);
  const [saving, setSaving]         = useState(false);

  if (scores.length === 0) {
    return (
      <div className="bg-gray-900 border border-gray-800 rounded p-4">
        <div className="text-[10px] text-gray-500 tracking-widest uppercase mb-2">AI Pair Selector</div>
        <div className="text-xs text-gray-600 text-center py-3 animate-pulse">
          Collecting market data…
        </div>
      </div>
    );
  }

  async function applyOverride() {
    if (manualPins.length === 0) return;
    setSaving(true);
    try {
      await fetch("/api/intelligence/override", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ symbols: manualPins }),
      });
    } finally {
      setSaving(false);
    }
  }

  async function restoreAuto() {
    setSaving(true);
    setManualPins([]);
    try {
      await fetch("/api/intelligence/toggle_auto", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: true }),
      });
    } finally {
      setSaving(false);
    }
  }

  function togglePin(sym) {
    setManualPins((prev) =>
      prev.includes(sym) ? prev.filter((s) => s !== sym) : [...prev, sym]
    );
  }

  const displayScores = scores.slice(0, 6);

  return (
    <div className="bg-gray-900 border border-gray-800 rounded p-4">
      {/* Header */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-gray-500 tracking-widest uppercase">AI Pair Selector</span>
          <span className={`text-[7px] px-1.5 py-0.5 rounded font-bold tracking-widest border ${
            autoSelect && manualPins.length === 0
              ? "bg-green-950/40 border-green-900/60 text-green-500"
              : "bg-yellow-950/40 border-yellow-900/60 text-yellow-600"
          }`}>
            {autoSelect && manualPins.length === 0 ? "AUTO" : "MANUAL"}
          </span>
        </div>
        <span className="text-[8px] text-gray-700 font-mono">
          top {displayScores.length} of {scores.length}
        </span>
      </div>

      {/* Current AI selection banner */}
      {selected.length > 0 && (
        <div className="bg-cyan-950/20 border border-cyan-900/40 rounded px-2 py-1 mb-2">
          <span className="text-[9px] text-cyan-600">
            Trading: {selected.join(", ")}
          </span>
        </div>
      )}

      {/* Score list */}
      <div className="divide-y divide-gray-800/0">
        {displayScores.map((item, i) => (
          <ScoreRow
            key={item.symbol}
            item={item}
            index={i}
            selected={manualPins}
            onPinToggle={togglePin}
          />
        ))}
      </div>

      {/* Override controls */}
      <div className="mt-2 flex gap-2">
        {manualPins.length > 0 && (
          <button
            onClick={applyOverride}
            disabled={saving}
            className="flex-1 text-[9px] py-1.5 rounded border border-cyan-800 text-cyan-400
                       hover:bg-cyan-900/30 transition-colors font-bold tracking-wide"
          >
            {saving ? "Applying…" : `Override → ${manualPins.join(", ")}`}
          </button>
        )}
        {(!autoSelect || manualPins.length > 0) && (
          <button
            onClick={restoreAuto}
            disabled={saving}
            className="flex-1 text-[9px] py-1.5 rounded border border-gray-700 text-gray-500
                       hover:bg-gray-800/40 transition-colors"
          >
            Restore Auto
          </button>
        )}
      </div>

      <div className="text-[8px] text-gray-700 mt-2 text-center">
        Click a row to expand · ○ pin to override AI pick
      </div>
    </div>
  );
}
