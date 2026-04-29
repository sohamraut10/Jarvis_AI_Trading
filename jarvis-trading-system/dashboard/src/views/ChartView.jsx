import { useEffect, useRef, useState, useCallback } from "react";
import { createChart, CrosshairMode, LineStyle } from "lightweight-charts";

const TIMEFRAMES = ["1min", "5min", "15min"];

const MARKER_COLORS = {
  signal_BUY:    { color: "#22d3ee",   shape: "arrowUp",   position: "belowBar" },
  signal_SELL:   { color: "#f87171",   shape: "arrowDown", position: "aboveBar" },
  decision_BUY:  { color: "#a78bfa",   shape: "circle",    position: "belowBar" },
  decision_SELL: { color: "#fb923c",   shape: "circle",    position: "aboveBar" },
  trade_BUY:     { color: "#4ade80",   shape: "arrowUp",   position: "belowBar" },
  trade_SELL:    { color: "#ef4444",   shape: "arrowDown", position: "aboveBar" },
};

function markerStyle(m) {
  const key = m.type === "trade"
    ? `trade_${m.side}`
    : m.type === "decision"
      ? `decision_${m.action}`
      : `signal_${m.side}`;
  return MARKER_COLORS[key] || { color: "#94a3b8", shape: "circle", position: "aboveBar" };
}

function markerText(m) {
  if (m.type === "trade")    return `${m.side} ${m.qty}@₹${m.price}`;
  if (m.type === "decision") return `${m.model || "AI"} ${m.action}`;
  const conf = m.confidence != null ? ` ${Math.round(m.confidence * 100)}%` : "";
  return `${m.strategy}${conf}`;
}

export default function ChartView({ snapshot }) {
  const chartRef    = useRef(null);
  const chartObj    = useRef(null);
  const candleSeries = useRef(null);
  const volSeries   = useRef(null);

  const [symbol, setSymbol]   = useState("");
  const [tf, setTf]           = useState("5min");
  const [data, setData]       = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError]     = useState(null);
  const [legendText, setLegendText] = useState("");

  // Build symbol list from scanner snapshot
  const symbols = Object.keys(snapshot?.scanner ?? {}).sort();

  useEffect(() => {
    if (!symbol && symbols.length) setSymbol(symbols[0]);
  }, [symbols, symbol]);

  // Fetch chart data
  const fetchChart = useCallback(async () => {
    if (!symbol) return;
    setLoading(true);
    setError(null);
    try {
      const res  = await fetch(`/api/charts/${symbol}?tf=${tf}`);
      const json = await res.json();
      setData(json);
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [symbol, tf]);

  useEffect(() => { fetchChart(); }, [fetchChart]);

  // Auto-refresh every 30s
  useEffect(() => {
    const id = setInterval(fetchChart, 30_000);
    return () => clearInterval(id);
  }, [fetchChart]);

  // Build / rebuild chart when data changes
  useEffect(() => {
    if (!chartRef.current || !data) return;

    // Create chart once
    if (!chartObj.current) {
      chartObj.current = createChart(chartRef.current, {
        layout: { background: { color: "#030712" }, textColor: "#94a3b8" },
        grid:   { vertLines: { color: "#1f2937" }, horzLines: { color: "#1f2937" } },
        crosshair: { mode: CrosshairMode.Normal },
        rightPriceScale: { borderColor: "#374151" },
        timeScale: { borderColor: "#374151", timeVisible: true, secondsVisible: false },
        width:  chartRef.current.clientWidth,
        height: chartRef.current.clientHeight,
      });

      candleSeries.current = chartObj.current.addCandlestickSeries({
        upColor: "#4ade80", downColor: "#ef4444",
        borderUpColor: "#4ade80", borderDownColor: "#ef4444",
        wickUpColor: "#4ade80", wickDownColor: "#ef4444",
      });

      volSeries.current = chartObj.current.addHistogramSeries({
        color: "#1e3a5f",
        priceFormat: { type: "volume" },
        priceScaleId: "volume",
        scaleMargins: { top: 0.8, bottom: 0 },
      });

      // Crosshair legend
      chartObj.current.subscribeCrosshairMove((p) => {
        if (!p.time || !p.seriesData) { setLegendText(""); return; }
        const bar = p.seriesData.get(candleSeries.current);
        if (bar) {
          setLegendText(
            `O ${bar.open?.toFixed(2)}  H ${bar.high?.toFixed(2)}  ` +
            `L ${bar.low?.toFixed(2)}  C ${bar.close?.toFixed(2)}`
          );
        }
      });
    }

    // Feed bars
    if (data.bars?.length) {
      candleSeries.current.setData(data.bars);
      volSeries.current.setData(
        data.bars.map((b) => ({ time: b.time, value: b.volume, color: b.close >= b.open ? "#14532d55" : "#7f1d1d55" }))
      );
    }

    // Feed markers
    if (data.markers?.length) {
      const markers = data.markers.map((m) => {
        const style = markerStyle(m);
        return {
          time:     m.time,
          position: style.position,
          color:    style.color,
          shape:    style.shape,
          text:     markerText(m),
          size:     m.type === "trade" ? 2 : 1,
        };
      });
      // lightweight-charts requires markers sorted by time
      markers.sort((a, b) => a.time - b.time);
      candleSeries.current.setMarkers(markers);
    } else {
      candleSeries.current.setMarkers([]);
    }

    chartObj.current.timeScale().fitContent();
  }, [data]);

  // Resize observer
  useEffect(() => {
    if (!chartRef.current || !chartObj.current) return;
    const ro = new ResizeObserver(() => {
      chartObj.current?.applyOptions({
        width:  chartRef.current.clientWidth,
        height: chartRef.current.clientHeight,
      });
    });
    ro.observe(chartRef.current);
    return () => ro.disconnect();
  }, []);

  // Cleanup on unmount
  useEffect(() => () => { chartObj.current?.remove(); chartObj.current = null; }, []);

  const signalCount   = data?.markers?.filter((m) => m.type === "signal").length   ?? 0;
  const decisionCount = data?.markers?.filter((m) => m.type === "decision").length ?? 0;
  const tradeCount    = data?.markers?.filter((m) => m.type === "trade").length    ?? 0;

  return (
    <div className="flex flex-col h-full bg-gray-950 text-gray-100 p-4 gap-3">
      {/* Controls */}
      <div className="flex flex-wrap items-center gap-3">
        <select
          value={symbol}
          onChange={(e) => setSymbol(e.target.value)}
          className="bg-gray-900 border border-gray-700 text-cyan-300 rounded px-3 py-1.5 text-sm font-mono"
        >
          {symbols.map((s) => <option key={s}>{s}</option>)}
          {!symbols.length && <option value="">— no symbols —</option>}
        </select>

        <div className="flex gap-1">
          {TIMEFRAMES.map((t) => (
            <button
              key={t}
              onClick={() => setTf(t)}
              className={`px-3 py-1.5 rounded text-xs font-mono border transition-colors ${
                tf === t
                  ? "bg-cyan-900 border-cyan-500 text-cyan-300"
                  : "bg-gray-900 border-gray-700 text-gray-400 hover:border-gray-500"
              }`}
            >
              {t}
            </button>
          ))}
        </div>

        <button
          onClick={fetchChart}
          className="px-3 py-1.5 rounded text-xs font-mono bg-gray-800 border border-gray-700 text-gray-300 hover:border-cyan-700"
        >
          {loading ? "Loading…" : "↺ Refresh"}
        </button>

        {/* Legend: marker types */}
        <div className="flex gap-3 ml-auto text-xs font-mono">
          <span className="text-cyan-400">▲ Strategy signal ({signalCount})</span>
          <span className="text-purple-400">● AI decision ({decisionCount})</span>
          <span className="text-green-400">▲ Trade fill ({tradeCount})</span>
        </div>
      </div>

      {/* OHLCV crosshair legend */}
      <div className="font-mono text-xs text-gray-400 h-4">
        {symbol && <span className="text-cyan-300 mr-3">{symbol}</span>}
        {legendText || (data?.bars?.length ? `${data.bars.length} bars · ${data.markers?.length ?? 0} markers` : "")}
      </div>

      {/* Error */}
      {error && (
        <div className="text-red-400 text-xs font-mono">Error: {error}</div>
      )}

      {/* Chart container */}
      <div
        ref={chartRef}
        className="flex-1 rounded-lg overflow-hidden border border-gray-800"
        style={{ minHeight: 400 }}
      />

      {/* Marker table */}
      {data?.markers?.length > 0 && (
        <div className="bg-gray-900 rounded-lg border border-gray-800 max-h-48 overflow-y-auto">
          <table className="w-full text-xs font-mono">
            <thead className="sticky top-0 bg-gray-900 border-b border-gray-800">
              <tr className="text-gray-500">
                <th className="px-3 py-1.5 text-left">Time</th>
                <th className="px-3 py-1.5 text-left">Type</th>
                <th className="px-3 py-1.5 text-left">Side</th>
                <th className="px-3 py-1.5 text-left">Strategy / Model</th>
                <th className="px-3 py-1.5 text-right">Price</th>
                <th className="px-3 py-1.5 text-right">Conf</th>
              </tr>
            </thead>
            <tbody>
              {[...data.markers].reverse().map((m, i) => {
                const style = markerStyle(m);
                const ts = new Date(m.time * 1000).toLocaleTimeString("en-IN", { hour12: false });
                return (
                  <tr key={i} className="border-b border-gray-800/50 hover:bg-gray-800/30">
                    <td className="px-3 py-1 text-gray-500">{ts}</td>
                    <td className="px-3 py-1">
                      <span style={{ color: style.color }} className="capitalize">{m.type}</span>
                    </td>
                    <td className="px-3 py-1" style={{ color: style.color }}>
                      {m.side || m.action || "—"}
                    </td>
                    <td className="px-3 py-1 text-gray-300">
                      {m.strategy || m.model || "—"}
                    </td>
                    <td className="px-3 py-1 text-right text-gray-200">
                      {m.price ? `₹${Number(m.price).toFixed(2)}` : "—"}
                    </td>
                    <td className="px-3 py-1 text-right text-gray-400">
                      {m.confidence != null ? `${Math.round(m.confidence * 100)}%` : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {!data?.bars?.length && !loading && (
        <div className="flex-1 flex items-center justify-center text-gray-600 font-mono text-sm">
          No bars yet — bars accumulate as ticks flow in (1 bar per {tf})
        </div>
      )}
    </div>
  );
}
