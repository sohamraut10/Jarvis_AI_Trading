/**
 * Trade Journal — review every closed trade, add notes, analyse mistakes.
 *
 * Data flow: GET /api/journal  → table of closed trades from SQLite
 *            POST /api/journal/{id}/note  → save/update a note
 */
import { useCallback, useEffect, useRef, useState } from "react";

const API = `${window.location.protocol}//${window.location.hostname}:8766`;

function fmt(n, dec = 2) {
  if (n == null) return "—";
  return Number(n).toFixed(dec);
}

function fmtTime(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso.replace(" ", "T") + (iso.includes("Z") ? "" : "Z"));
    return d.toLocaleTimeString("en-IN", { hour12: false, hour: "2-digit", minute: "2-digit" });
  } catch { return iso.slice(11, 16) || "—"; }
}

function fmtDate(iso) {
  if (!iso) return "—";
  return iso.slice(0, 10);
}

function pnlColor(pnl) {
  if (pnl > 0)  return "text-green-400";
  if (pnl < 0)  return "text-red-400";
  return "text-gray-400";
}

function sideBadge(side) {
  if (!side) return null;
  const isLong = side.toUpperCase() === "LONG" || side.toUpperCase() === "BUY";
  return (
    <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold tracking-widest ${
      isLong ? "bg-green-900/60 text-green-400" : "bg-red-900/60 text-red-400"
    }`}>
      {isLong ? "LONG" : "SHORT"}
    </span>
  );
}

// ── Top-level stats strip ──────────────────────────────────────────────────────
function StatsBar({ trades }) {
  if (!trades.length) return null;
  const wins     = trades.filter((t) => t.pnl > 0).length;
  const totalPnl = trades.reduce((s, t) => s + (t.pnl || 0), 0);
  const winRate  = trades.length ? ((wins / trades.length) * 100).toFixed(1) : 0;
  const best     = Math.max(...trades.map((t) => t.pnl));
  const worst    = Math.min(...trades.map((t) => t.pnl));
  const avgWin   = wins ? trades.filter((t) => t.pnl > 0).reduce((s, t) => s + t.pnl, 0) / wins : 0;
  const losses   = trades.length - wins;
  const avgLoss  = losses ? trades.filter((t) => t.pnl <= 0).reduce((s, t) => s + t.pnl, 0) / losses : 0;

  const stats = [
    { label: "Trades",    value: trades.length,                    color: "text-gray-300" },
    { label: "Win Rate",  value: `${winRate}%`,                    color: wins / trades.length >= 0.5 ? "text-green-400" : "text-red-400" },
    { label: "Total P&L", value: `₹${fmt(totalPnl)}`,              color: pnlColor(totalPnl) },
    { label: "Best",      value: `₹${fmt(best)}`,                  color: "text-green-400" },
    { label: "Worst",     value: `₹${fmt(worst)}`,                 color: "text-red-400" },
    { label: "Avg Win",   value: `₹${fmt(avgWin)}`,                color: "text-green-300" },
    { label: "Avg Loss",  value: `₹${fmt(Math.abs(avgLoss))}`,     color: "text-red-300" },
  ];

  return (
    <div className="flex flex-wrap gap-4 px-4 py-3 border-b border-gray-800 bg-gray-900/40">
      {stats.map((s) => (
        <div key={s.label} className="flex flex-col items-center min-w-[80px]">
          <span className={`text-sm font-bold font-mono ${s.color}`}>{s.value}</span>
          <span className="text-[10px] text-gray-600 tracking-widest uppercase">{s.label}</span>
        </div>
      ))}
    </div>
  );
}

// ── Note editor ───────────────────────────────────────────────────────────────
function NoteEditor({ trade, onSaved }) {
  const [text, setText]   = useState(trade.notes || "");
  const [saving, setSaving] = useState(false);
  const [status, setStatus] = useState("");

  const save = useCallback(async () => {
    setSaving(true);
    setStatus("");
    try {
      const res = await fetch(`${API}/api/journal/${trade.id}/note`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ note: text }),
      });
      if (res.ok) {
        setStatus("saved");
        onSaved(trade.id, text);
        setTimeout(() => setStatus(""), 2000);
      } else {
        setStatus("error");
      }
    } catch {
      setStatus("error");
    }
    setSaving(false);
  }, [trade.id, text, onSaved]);

  return (
    <div className="mt-2">
      <textarea
        className="w-full bg-gray-900 border border-gray-700 rounded text-xs text-gray-200 font-mono
                   p-2 resize-none focus:outline-none focus:border-cyan-700 placeholder-gray-700"
        rows={3}
        placeholder="Add a note — what went wrong, what worked, lessons learnt…"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="flex items-center gap-3 mt-1">
        <button
          onClick={save}
          disabled={saving}
          className="px-3 py-1 text-[10px] font-bold tracking-widest bg-cyan-800/60 hover:bg-cyan-700/80
                     text-cyan-300 rounded transition-colors disabled:opacity-50"
        >
          {saving ? "SAVING…" : "SAVE NOTE"}
        </button>
        {status === "saved" && <span className="text-[10px] text-green-400">✓ Saved</span>}
        {status === "error" && <span className="text-[10px] text-red-400">✗ Error</span>}
      </div>
    </div>
  );
}

// ── Single trade row ──────────────────────────────────────────────────────────
function TradeRow({ trade, onNoteSaved }) {
  const [expanded, setExpanded] = useState(false);
  const hasNote = Boolean(trade.notes);

  return (
    <>
      <tr
        onClick={() => setExpanded((v) => !v)}
        className={`cursor-pointer border-b border-gray-800/60 hover:bg-gray-800/40 transition-colors text-xs font-mono ${
          trade.win ? "bg-green-950/10" : "bg-red-950/10"
        }`}
      >
        <td className="px-3 py-2 text-gray-500">{fmtDate(trade.entry_time)}</td>
        <td className="px-3 py-2 text-gray-400">{fmtTime(trade.entry_time)}</td>
        <td className="px-3 py-2 text-gray-400">{fmtTime(trade.exit_time)}</td>
        <td className="px-3 py-2 text-gray-200 font-bold">{trade.symbol}</td>
        <td className="px-3 py-2">{sideBadge(trade.side)}</td>
        <td className="px-3 py-2 text-gray-400">{trade.qty}</td>
        <td className="px-3 py-2 text-gray-300">₹{fmt(trade.entry, 2)}</td>
        <td className="px-3 py-2 text-gray-300">₹{fmt(trade.exit, 2)}</td>
        <td className={`px-3 py-2 font-bold ${pnlColor(trade.pnl)}`}>
          {trade.pnl >= 0 ? "+" : ""}₹{fmt(trade.pnl)}
        </td>
        <td className={`px-3 py-2 ${pnlColor(trade.pnl_pct)}`}>
          {trade.pnl_pct >= 0 ? "+" : ""}{fmt(trade.pnl_pct)}%
        </td>
        <td className="px-3 py-2 text-gray-600 capitalize">{trade.regime || "—"}</td>
        <td className="px-3 py-2 text-gray-600 truncate max-w-[120px]">{trade.strategy || "—"}</td>
        <td className="px-3 py-2 text-center">
          {hasNote
            ? <span className="text-yellow-400 text-[10px]">● note</span>
            : <span className="text-gray-700 text-[10px]">+ note</span>}
        </td>
      </tr>

      {expanded && (
        <tr className="border-b border-gray-700/60 bg-gray-900/60">
          <td colSpan={13} className="px-4 py-3">
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-[11px] mb-3">
              <div>
                <span className="text-gray-600">SL</span>
                <span className="ml-2 text-gray-300 font-mono">{trade.sl != null ? `₹${fmt(trade.sl)}` : "—"}</span>
              </div>
              <div>
                <span className="text-gray-600">TP</span>
                <span className="ml-2 text-gray-300 font-mono">{trade.tp != null ? `₹${fmt(trade.tp)}` : "—"}</span>
              </div>
              <div>
                <span className="text-gray-600">Confidence</span>
                <span className="ml-2 text-gray-300 font-mono">
                  {trade.confidence != null ? `${(trade.confidence * 100).toFixed(0)}%` : "—"}
                </span>
              </div>
              <div>
                <span className="text-gray-600">R/R target</span>
                <span className="ml-2 text-gray-300 font-mono">
                  {(trade.sl && trade.tp && trade.entry)
                    ? fmt(Math.abs(trade.tp - trade.entry) / Math.abs(trade.entry - trade.sl))
                    : "—"}
                </span>
              </div>
            </div>
            <NoteEditor trade={trade} onSaved={onNoteSaved} />
          </td>
        </tr>
      )}
    </>
  );
}

// ── Main Journal view ─────────────────────────────────────────────────────────
export default function Journal() {
  const [trades, setTrades]       = useState([]);
  const [loading, setLoading]     = useState(true);
  const [error, setError]         = useState(null);
  const [days, setDays]           = useState(30);
  const [filterSide, setFilterSide] = useState("ALL");
  const [filterWin, setFilterWin]   = useState("ALL");
  const [search, setSearch]         = useState("");
  const pollRef = useRef(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/journal?days=${days}&limit=500`);
      const data = await res.json();
      setTrades(data.trades ?? []);
      setError(null);
    } catch (e) {
      setError("Cannot reach backend — is the server running?");
    } finally {
      setLoading(false);
    }
  }, [days]);

  useEffect(() => {
    setLoading(true);
    load();
    pollRef.current = setInterval(load, 15000);
    return () => clearInterval(pollRef.current);
  }, [load]);

  const handleNoteSaved = useCallback((id, note) => {
    setTrades((prev) => prev.map((t) => (t.id === id ? { ...t, notes: note } : t)));
  }, []);

  const filtered = trades.filter((t) => {
    if (filterSide !== "ALL" && t.side?.toUpperCase() !== filterSide) return false;
    if (filterWin === "WIN"  && !t.win)  return false;
    if (filterWin === "LOSS" &&  t.win)  return false;
    if (search) {
      const q = search.toLowerCase();
      if (!t.symbol?.toLowerCase().includes(q) && !t.strategy?.toLowerCase().includes(q)) return false;
    }
    return true;
  });

  return (
    <div className="flex flex-col h-full bg-gray-950 text-gray-100 font-mono overflow-hidden">
      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div className="flex flex-wrap items-center gap-3 px-4 py-2.5 border-b border-gray-800 bg-gray-900/60 shrink-0">
        <span className="text-xs font-bold tracking-widest text-cyan-500">TRADE JOURNAL</span>

        {/* Days filter */}
        <select
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
          className="bg-gray-800 border border-gray-700 rounded text-xs text-gray-300 px-2 py-1"
        >
          {[7, 14, 30, 60, 90].map((d) => (
            <option key={d} value={d}>Last {d} days</option>
          ))}
        </select>

        {/* Side filter */}
        {["ALL", "LONG", "SHORT"].map((s) => (
          <button
            key={s}
            onClick={() => setFilterSide(s)}
            className={`px-2 py-1 rounded text-[10px] font-bold tracking-widest transition-colors ${
              filterSide === s
                ? "bg-cyan-800/60 text-cyan-300"
                : "text-gray-600 hover:text-gray-400"
            }`}
          >
            {s}
          </button>
        ))}

        {/* Win/Loss filter */}
        {["ALL", "WIN", "LOSS"].map((w) => (
          <button
            key={w}
            onClick={() => setFilterWin(w)}
            className={`px-2 py-1 rounded text-[10px] font-bold tracking-widest transition-colors ${
              filterWin === w
                ? w === "WIN" ? "bg-green-900/60 text-green-300" : w === "LOSS" ? "bg-red-900/60 text-red-300" : "bg-cyan-800/60 text-cyan-300"
                : "text-gray-600 hover:text-gray-400"
            }`}
          >
            {w}
          </button>
        ))}

        {/* Symbol search */}
        <input
          type="text"
          placeholder="Search symbol / strategy…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="bg-gray-800 border border-gray-700 rounded text-xs text-gray-300 px-2 py-1 w-48
                     focus:outline-none focus:border-cyan-700 placeholder-gray-700"
        />

        <button
          onClick={load}
          className="ml-auto px-3 py-1 text-[10px] font-bold tracking-widest text-gray-500
                     hover:text-cyan-400 transition-colors"
        >
          ↻ REFRESH
        </button>

        <span className="text-[10px] text-gray-700">{filtered.length} trades</span>
      </div>

      {/* ── Stats ──────────────────────────────────────────────────────── */}
      <StatsBar trades={filtered} />

      {/* ── Table ──────────────────────────────────────────────────────── */}
      <div className="flex-1 overflow-auto">
        {loading && (
          <div className="flex items-center justify-center h-32 text-gray-600 text-xs animate-pulse">
            Loading journal…
          </div>
        )}

        {error && (
          <div className="p-6 text-center text-red-500 text-xs">{error}</div>
        )}

        {!loading && !error && filtered.length === 0 && (
          <div className="flex flex-col items-center justify-center h-48 gap-2">
            <span className="text-4xl opacity-20">📒</span>
            <span className="text-gray-600 text-xs">No closed trades yet — they'll appear here as positions are closed.</span>
          </div>
        )}

        {!loading && !error && filtered.length > 0 && (
          <table className="w-full text-left border-collapse">
            <thead className="sticky top-0 bg-gray-900 border-b border-gray-700">
              <tr className="text-[10px] text-gray-500 tracking-widest uppercase">
                <th className="px-3 py-2">Date</th>
                <th className="px-3 py-2">Entry</th>
                <th className="px-3 py-2">Exit</th>
                <th className="px-3 py-2">Symbol</th>
                <th className="px-3 py-2">Side</th>
                <th className="px-3 py-2">Qty</th>
                <th className="px-3 py-2">Entry ₹</th>
                <th className="px-3 py-2">Exit ₹</th>
                <th className="px-3 py-2">P&L</th>
                <th className="px-3 py-2">P&L %</th>
                <th className="px-3 py-2">Regime</th>
                <th className="px-3 py-2">Strategy</th>
                <th className="px-3 py-2">Notes</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((t) => (
                <TradeRow key={t.id} trade={t} onNoteSaved={handleNoteSaved} />
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
