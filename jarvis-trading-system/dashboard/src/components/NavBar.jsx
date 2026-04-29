import { useEffect } from "react";

const VIEWS = [
  { id: "mission", label: "Mission Control" },
  { id: "arena",   label: "Strategy Arena" },
  { id: "ledger",  label: "Trade Ledger" },
  { id: "brain",   label: "Brain Analytics" },
  { id: "control", label: "Control Room" },
  { id: "log",     label: "Command Log" },
  { id: "chart",   label: "Live Chart" },
];

export { VIEWS };

export default function NavBar({ active, onChange, onSearchOpen }) {
  // Global "/" shortcut — skip when focus is on a text field
  useEffect(() => {
    const handler = (e) => {
      if (e.key !== "/") return;
      const tag = document.activeElement?.tagName ?? "";
      if (["INPUT", "TEXTAREA", "SELECT"].includes(tag)) return;
      e.preventDefault();
      onSearchOpen?.();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onSearchOpen]);

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

      {/* Spacer pushes search button to the right */}
      <div className="flex-1" />

      {/* Search button */}
      <button
        onClick={onSearchOpen}
        className="flex items-center gap-2 px-3 py-1.5 my-1 rounded-lg border border-gray-800
                   text-gray-600 hover:text-gray-300 hover:border-gray-700 transition-colors shrink-0"
      >
        {/* Magnifier SVG */}
        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
          <circle cx="11" cy="11" r="8" />
          <path d="M21 21l-4.35-4.35" strokeLinecap="round" />
        </svg>

        <span className="hidden sm:inline text-xs font-mono">Search</span>
        <kbd className="hidden sm:block text-[9px] border border-gray-700 rounded px-1 py-0.5">/</kbd>
      </button>
    </nav>
  );
}
