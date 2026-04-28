import { MODE_LABEL, MODE_STYLE, MODES } from "../hooks/useAutonomy";

const NOTIF_CATS = [
  { key: "REGIME",   label: "🔵 Regime changes" },
  { key: "ROTATION", label: "🟡 Strategy rotations" },
  { key: "ENTRY",    label: "🟢 Entry signals" },
  { key: "EXIT",     label: "✅ Exit / wins" },
  { key: "SIZING",   label: "📐 Position sizing" },
  { key: "BRAIN",    label: "🧠 Brain retrains" },
  { key: "APPROVAL", label: "⚡ Approval requests" },
  { key: "RISK",     label: "🚨 Risk / kill switch" },
];

function ModeCard({ id, active, onClick }) {
  const activated = active === id;
  return (
    <button
      onClick={() => onClick(id)}
      className={[
        "flex-1 py-4 rounded border-2 font-bold tracking-[0.2em] text-sm transition-all",
        activated ? MODE_STYLE[id] : "border-gray-800 text-gray-700 hover:border-gray-600 hover:text-gray-500",
      ].join(" ")}
    >
      {MODE_LABEL[id]}
    </button>
  );
}

function Slider({ label, min, max, step, value, onChange, fmt }) {
  return (
    <div className="py-2">
      <div className="flex justify-between mb-1">
        <span className="text-[10px] text-gray-500 tracking-widest uppercase">{label}</span>
        <span className="text-[10px] font-mono text-gray-300 tabular-nums">{fmt ? fmt(value) : value}</span>
      </div>
      <input
        type="range" min={min} max={max} step={step} value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-cyan-500 cursor-pointer"
      />
      <div className="flex justify-between text-[9px] text-gray-700 mt-0.5">
        <span>{fmt ? fmt(min) : min}</span>
        <span>{fmt ? fmt(max) : max}</span>
      </div>
    </div>
  );
}

export default function ControlRoom({
  mode, changeMode,
  approvalTimeout, setApprovalTimeout,
  riskParams, setRiskParams,
  filters, toggleFilter,
}) {
  const pct = (v) => `${(v * 100).toFixed(0)}%`;
  const secs = (v) => `${v}s`;

  return (
    <div className="p-4 grid grid-cols-1 lg:grid-cols-3 gap-4">
      {/* Autonomy mode selector */}
      <div className="lg:col-span-3 bg-gray-900 border border-gray-800 rounded p-4">
        <div className="text-[10px] text-gray-500 tracking-widest uppercase mb-4">Autonomy Mode</div>
        <div className="flex gap-3">
          {MODES.map((m) => (
            <ModeCard key={m} id={m} active={mode} onClick={changeMode} />
          ))}
        </div>
        <div className="mt-3 text-[10px] text-gray-600 space-y-0.5">
          <div><span className="text-gray-500 font-bold">MANUAL</span> — JARVIS signals only, you place all orders</div>
          <div><span className="text-yellow-600 font-bold">SEMI AUTO</span> — JARVIS acts autonomously, but shows approval banner for each decision</div>
          <div><span className="text-cyan-600 font-bold">FULL AUTO</span> — JARVIS trades autonomously within risk limits, no interruptions</div>
        </div>
      </div>

      {/* Risk parameters */}
      <div className="lg:col-span-2 bg-gray-900 border border-gray-800 rounded p-4">
        <div className="text-[10px] text-gray-500 tracking-widest uppercase mb-2">Risk Parameters</div>
        <Slider
          label="Kill switch drawdown %"
          min={0.01} max={0.10} step={0.005} value={riskParams.killSwitchPct}
          onChange={(v) => setRiskParams((p) => ({ ...p, killSwitchPct: v }))}
          fmt={pct}
        />
        <Slider
          label="Kelly fraction"
          min={0.1} max={1.0} step={0.05} value={riskParams.kellyFraction}
          onChange={(v) => setRiskParams((p) => ({ ...p, kellyFraction: v }))}
          fmt={(v) => `${v.toFixed(2)}×`}
        />
        <Slider
          label="Max single trade %"
          min={0.05} max={0.30} step={0.01} value={riskParams.maxTradePct}
          onChange={(v) => setRiskParams((p) => ({ ...p, maxTradePct: v }))}
          fmt={pct}
        />
        <Slider
          label="Max symbol concentration %"
          min={0.10} max={0.50} step={0.05} value={riskParams.maxConcentration}
          onChange={(v) => setRiskParams((p) => ({ ...p, maxConcentration: v }))}
          fmt={pct}
        />
        <Slider
          label="Semi-auto approval timeout"
          min={10} max={120} step={5} value={approvalTimeout}
          onChange={setApprovalTimeout}
          fmt={secs}
        />

        <div className="mt-4 text-[9px] text-gray-700 border-t border-gray-800 pt-3">
          ⚠ Changes are applied to the engine over WebSocket. The hard kill-switch
          threshold is enforced server-side and cannot be disabled from this UI.
        </div>
      </div>

      {/* Notification filters */}
      <div className="bg-gray-900 border border-gray-800 rounded p-4">
        <div className="text-[10px] text-gray-500 tracking-widest uppercase mb-3">Notifications</div>
        <div className="space-y-2">
          {NOTIF_CATS.map(({ key, label }) => (
            <label key={key} className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={filters[key] ?? true}
                onChange={() => toggleFilter(key)}
                className="accent-cyan-500 w-3.5 h-3.5"
              />
              <span className="text-xs text-gray-400">{label}</span>
            </label>
          ))}
        </div>
        <div className="mt-4 pt-3 border-t border-gray-800">
          <button
            onClick={() => {
              if ("Notification" in window) Notification.requestPermission();
            }}
            className="text-[10px] text-cyan-600 hover:text-cyan-400 transition-colors"
          >
            Enable push notifications →
          </button>
        </div>
      </div>
    </div>
  );
}
