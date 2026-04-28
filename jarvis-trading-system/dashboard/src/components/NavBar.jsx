const VIEWS = [
  { id: "mission", label: "Mission Control" },
  { id: "arena",   label: "Strategy Arena" },
  { id: "ledger",  label: "Trade Ledger" },
  { id: "brain",   label: "Brain Analytics" },
  { id: "control", label: "Control Room" },
  { id: "log",     label: "Command Log" },
];

export { VIEWS };

export default function NavBar({ active, onChange }) {
  return (
    <nav className="flex items-center gap-1 px-4 border-b border-gray-800 bg-gray-950 shrink-0 overflow-x-auto">
      {VIEWS.map((v, i) => (
        <button
          key={v.id}
          onClick={() => onChange(v.id)}
          className={[
            "px-4 py-2.5 text-xs font-bold tracking-widest shrink-0 border-b-2 transition-colors",
            active === v.id
              ? "border-cyan-500 text-cyan-400"
              : "border-transparent text-gray-600 hover:text-gray-400",
          ].join(" ")}
        >
          <span className="text-gray-700 mr-1.5 font-mono">{i + 1}</span>
          {v.label.toUpperCase()}
        </button>
      ))}
    </nav>
  );
}
