import { useCallback, useEffect, useRef, useState } from "react";

// ── Constants ─────────────────────────────────────────────────────────────────

const RECENT_KEY   = "jarvis_recent_instruments";
const MAX_RECENT   = 8;
const DEBOUNCE_MS  = 250;

const GROUP_ORDER = ["EQ", "FUT", "OPT", "IDX"];
const GROUP_LABEL = {
  EQ:  "Equities",
  FUT: "Futures",
  OPT: "Options",
  IDX: "Indices & ETF",
};

const BADGE_CLS = {
  EQ:  "text-blue-300  border-blue-300/20  bg-blue-900/20",
  FUT: "text-orange-300 border-orange-300/20 bg-orange-900/20",
  OPT: "text-purple-300 border-purple-300/20 bg-purple-900/20",
  IDX: "text-gray-300  border-gray-300/30  bg-gray-800/30",
};

const STATUS_DOT = {
  live:      "bg-green-400  animate-pulse",
  stale:     "bg-yellow-400",
  searching: "bg-cyan-400   animate-pulse",
  offline:   "bg-gray-600",
};

// ── Local-storage helpers ─────────────────────────────────────────────────────

function loadRecent() {
  try {
    return JSON.parse(localStorage.getItem(RECENT_KEY) ?? "[]");
  } catch {
    return [];
  }
}

function saveRecent(items) {
  try {
    localStorage.setItem(RECENT_KEY, JSON.stringify(items.slice(0, MAX_RECENT)));
  } catch {}
}

function pushRecent(inst) {
  const prev  = loadRecent();
  const dedup = prev.filter((r) => r.symbol !== inst.symbol);
  saveRecent([inst, ...dedup]);
}

// ── Grouped results helper ────────────────────────────────────────────────────

function groupResults(results) {
  const map = {};
  results.forEach((r) => {
    const b = r.badge ?? "EQ";
    if (!map[b]) map[b] = [];
    map[b].push(r);
  });
  // Return flat list with injected header markers
  const flat = [];
  GROUP_ORDER.forEach((badge) => {
    if (!map[badge]?.length) return;
    flat.push({ _header: true, badge, count: map[badge].length });
    map[badge].forEach((r) => flat.push(r));
  });
  return flat;
}

// ── ResultRow ─────────────────────────────────────────────────────────────────

function ResultRow({ inst, scannerEntry, highlighted, onSelect }) {
  const rowRef = useRef(null);

  useEffect(() => {
    if (highlighted && rowRef.current) {
      rowRef.current.scrollIntoView({ block: "nearest" });
    }
  }, [highlighted]);

  const badge   = inst.badge ?? "EQ";
  const inScan  = !!scannerEntry;
  const status  = scannerEntry?.status;
  const ltp     = scannerEntry?.ltp;
  const isCurr  = scannerEntry?.is_currency;
  const priceStr = ltp != null
    ? (isCurr ? ltp.toFixed(4) : `₹${ltp.toFixed(2)}`)
    : "—";

  const subtitle = [
    inst.seg_label,
    inst.lot_size ? `Lot ${inst.lot_size}` : null,
    inst.expiry   ? inst.expiry : null,
    inst.strike && inst.option_type ? `${inst.strike} ${inst.option_type}` : null,
  ].filter(Boolean).join(" · ");

  return (
    <button
      ref={rowRef}
      onClick={onSelect}
      className={[
        "w-full flex items-center gap-3 px-4 py-2.5 text-left transition-colors",
        highlighted ? "bg-cyan-950/40" : "hover:bg-gray-800/60",
      ].join(" ")}
    >
      {/* Badge pill */}
      <span className={[
        "text-[8px] font-bold px-1.5 py-0.5 rounded border tracking-widest shrink-0",
        BADGE_CLS[badge] ?? BADGE_CLS.EQ,
      ].join(" ")}>
        {badge}
      </span>

      {/* Name + subtitle */}
      <div className="flex-1 min-w-0">
        <div className="text-xs font-mono font-bold text-gray-200 truncate">
          {inst.display || inst.symbol}
        </div>
        {subtitle && (
          <div className="text-[9px] text-gray-600 mt-0.5 truncate">{subtitle}</div>
        )}
      </div>

      {/* Right side: price+dot if subscribed, else +ADD */}
      {inScan ? (
        <div className="flex items-center gap-1.5 shrink-0">
          <span className="text-xs font-mono tabular-nums text-gray-300">{priceStr}</span>
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${STATUS_DOT[status] ?? STATUS_DOT.searching}`} />
        </div>
      ) : (
        <span className="text-[10px] text-gray-700 hover:text-cyan-500 shrink-0 font-mono transition-colors">
          + ADD
        </span>
      )}
    </button>
  );
}

// ── GlobalSearch ──────────────────────────────────────────────────────────────

export default function GlobalSearch({ snapshot, open, onClose }) {
  const [query,      setQuery]      = useState("");
  const [results,    setResults]    = useState([]);
  const [loading,    setLoading]    = useState(false);
  const [scripReady, setScripReady] = useState(true);  // true = unknown/ready; false = loading
  const [selected,   setSelected]   = useState(0);
  const [recent,     setRecent]     = useState([]);

  const debounceRef = useRef(null);
  const inputRef    = useRef(null);
  const scanner     = snapshot?.scanner ?? {};

  // ── Open/close effects ────────────────────────────────────────────────────

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setResults([]);
    setSelected(0);
    setRecent(loadRecent());

    // Focus input
    setTimeout(() => inputRef.current?.focus(), 50);

    // Check scrip status
    fetch("/api/instruments/scrip_status")
      .then((r) => r.json())
      .then((d) => setScripReady(!(d.loading)))
      .catch(() => setScripReady(true));
  }, [open]);

  // ── Escape key ────────────────────────────────────────────────────────────

  useEffect(() => {
    if (!open) return;
    const handler = (e) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [open, onClose]);

  // ── Debounced search ──────────────────────────────────────────────────────

  useEffect(() => {
    clearTimeout(debounceRef.current);
    if (!query.trim()) {
      setResults([]);
      setSelected(0);
      return;
    }
    debounceRef.current = setTimeout(async () => {
      setLoading(true);
      try {
        const r = await fetch(`/api/instruments/search?q=${encodeURIComponent(query.trim())}&limit=50`);
        const d = await r.json();
        setResults(d.results ?? []);
        setSelected(0);
      } catch {
        setResults([]);
      } finally {
        setLoading(false);
      }
    }, DEBOUNCE_MS);
    return () => clearTimeout(debounceRef.current);
  }, [query]);

  // ── Flat selectable rows (no header items) ────────────────────────────────

  const displayItems = query.trim()
    ? groupResults(results)
    : recent.length
      ? [{ _header: true, badge: "RECENT", count: recent.length, _label: "Recently Added" },
         ...recent]
      : [];

  const selectableItems = displayItems.filter((r) => !r._header);
  const totalSelectable  = selectableItems.length;

  // ── Keyboard nav ──────────────────────────────────────────────────────────

  const handleKeyDown = useCallback((e) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setSelected((p) => (p + 1) % Math.max(totalSelectable, 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setSelected((p) => (p - 1 + Math.max(totalSelectable, 1)) % Math.max(totalSelectable, 1));
    } else if (e.key === "Enter" && totalSelectable > 0) {
      e.preventDefault();
      handleSelect(selectableItems[selected]);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected, totalSelectable, selectableItems]);

  // ── Subscribe ─────────────────────────────────────────────────────────────

  const handleSelect = useCallback(async (inst) => {
    if (!inst || inst._header) return;
    const alreadyIn = !!scanner[inst.symbol];
    if (!alreadyIn) {
      await fetch("/api/instruments/subscribe", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          security_id:      inst.security_id,
          exchange_segment: inst.segment,
          symbol:           inst.symbol,
          lot_size:         inst.lot_size ?? 1,
        }),
      }).catch(() => {});
    }
    pushRecent(inst);
    onClose();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scanner, onClose]);

  // ── Render nothing when closed ────────────────────────────────────────────

  if (!open) return null;

  // Build display flat list and map selection index to items
  let selectIdx = 0;

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[10vh] bg-black/60 backdrop-blur-sm"
      onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="w-full max-w-lg bg-gray-950 border border-gray-800 rounded-xl shadow-2xl overflow-hidden flex flex-col max-h-[70vh]">

        {/* ── Search input ──────────────────────────────────────────── */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-gray-800">
          {/* Magnifier icon */}
          <svg className="w-4 h-4 text-gray-600 shrink-0" fill="none" stroke="currentColor" strokeWidth={2} viewBox="0 0 24 24">
            <circle cx="11" cy="11" r="8" />
            <path d="M21 21l-4.35-4.35" strokeLinecap="round" />
          </svg>

          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Search stocks, futures, options, currency…"
            className="flex-1 bg-transparent text-sm font-mono text-gray-200 placeholder-gray-700
                       focus:outline-none caret-cyan-400"
            spellCheck={false}
            autoComplete="off"
          />

          {/* Status indicator */}
          {loading && (
            <span className="text-[9px] text-cyan-400 animate-pulse font-mono shrink-0">scanning…</span>
          )}
          {!loading && !scripReady && !query.trim() && (
            <span className="text-[9px] text-yellow-700 font-mono shrink-0">loading index…</span>
          )}
        </div>

        {/* ── Results list ──────────────────────────────────────────── */}
        <div className="overflow-y-auto flex-1">
          {displayItems.length === 0 && !loading && (
            <div className="py-10 text-center space-y-2">
              <p className="text-xs text-gray-700">
                Type to search across ~100,000 instruments
              </p>
              {!scripReady && (
                <p className="text-[10px] text-yellow-800">Indexing ~100,000 instruments…</p>
              )}
            </div>
          )}

          {displayItems.map((item, rawIdx) => {
            if (item._header) {
              const label = item._label ?? GROUP_LABEL[item.badge] ?? item.badge;
              return (
                <div
                  key={`hdr-${item.badge}-${rawIdx}`}
                  className="sticky top-0 z-10 flex items-center justify-between
                             px-4 py-1.5 bg-gray-950 border-b border-gray-800/60"
                >
                  <span className="text-[9px] text-gray-600 tracking-widest uppercase font-bold">
                    {label}
                  </span>
                  <span className="text-[9px] text-gray-700 font-mono">{item.count}</span>
                </div>
              );
            }

            const myIdx = selectIdx++;
            const scanEntry = scanner[item.symbol];

            return (
              <ResultRow
                key={`${item.security_id ?? item.symbol}-${rawIdx}`}
                inst={item}
                scannerEntry={scanEntry}
                highlighted={myIdx === selected}
                onSelect={() => handleSelect(item)}
              />
            );
          })}
        </div>

        {/* ── Footer ────────────────────────────────────────────────── */}
        <div className="flex items-center justify-between px-4 py-2 border-t border-gray-800 bg-gray-950">
          <div className="flex items-center gap-3 text-[9px] text-gray-700 font-mono">
            <span><kbd className="border border-gray-700 rounded px-1 py-0.5">↑↓</kbd> navigate</span>
            <span><kbd className="border border-gray-700 rounded px-1 py-0.5">↵</kbd> subscribe</span>
            <span><kbd className="border border-gray-700 rounded px-1 py-0.5">esc</kbd> close</span>
          </div>
          {query.trim() && (
            <span className="text-[9px] text-gray-700 font-mono">
              {results.length} result{results.length !== 1 ? "s" : ""}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
