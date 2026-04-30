import { lazy, Suspense, useCallback, useEffect, useRef, useState } from "react";
import ApprovalBanner from "./components/ApprovalBanner";
import DrawdownBar from "./components/DrawdownBar";
import GlobalSearch from "./components/GlobalSearch";
import HUD from "./components/HUD";
import NavBar from "./components/NavBar";
import NotificationStack from "./components/NotificationStack";
import useAutonomy from "./hooks/useAutonomy";
import useNotifications from "./hooks/useNotifications";
import useWebSocket from "./hooks/useWebSocket";
import BrainAnalytics from "./views/BrainAnalytics";
import CommandLog from "./views/CommandLog";
import ControlRoom from "./views/ControlRoom";
import MissionControl from "./views/MissionControl";
import StrategyArena from "./views/StrategyArena";
import TradeLedger from "./views/TradeLedger";

const ChartView = lazy(() =>
  import("./views/ChartView").catch(() => ({
    default: () => (
      <div className="p-8 text-center text-yellow-600 font-mono text-sm">
        Chart unavailable — run: <code className="text-yellow-400">npm install</code> in the dashboard folder
      </div>
    ),
  }))
);

const WS_URL =
  import.meta.env.VITE_WS_URL ??
  `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws`;

const MAX_PNL_HISTORY  = 300;
const MAX_SIGNALS      = 100;
const MAX_ACTIVITY     = 200;

const DEFAULT_RISK = {
  killSwitchPct:    0.03,
  kellyFraction:    0.50,
  maxTradePct:      0.20,
  maxConcentration: 0.30,
};

function nowTime() {
  return new Date().toLocaleTimeString("en-IN", { hour12: false });
}

export default function App() {
  const { snapshot, connected, send } = useWebSocket(WS_URL);
  const autonomy = useAutonomy(send);
  const notifs   = useNotifications();

  const [view, setView]             = useState("mission");
  const [searchOpen, setSearchOpen] = useState(false);
  const [pnlHistory, setPnlHistory] = useState([]);
  const [signals, setSignals]       = useState([]);
  const [activity, setActivity]     = useState([]);
  const [riskParams, setRiskParams] = useState(DEFAULT_RISK);

  const seenSignalsRef   = useRef(new Set());
  const prevRegimeRef    = useRef(null);
  const prevConnectedRef = useRef(null);
  const prevTickTotal    = useRef(0);
  const prevPositions    = useRef({});

  function pushActivity(item) {
    setActivity((prev) => {
      const next = [...prev, { ...item, id: Date.now() + Math.random() }];
      return next.length > MAX_ACTIVITY ? next.slice(-MAX_ACTIVITY) : next;
    });
  }

  // ── Connection events ──────────────────────────────────────────────────────
  useEffect(() => {
    if (prevConnectedRef.current === null) {
      prevConnectedRef.current = connected;
      return;
    }
    if (connected && !prevConnectedRef.current) {
      pushActivity({ type: "connected", title: "Server connected", time: nowTime() });
    } else if (!connected && prevConnectedRef.current) {
      pushActivity({ type: "system", title: "Server disconnected", time: nowTime() });
    }
    prevConnectedRef.current = connected;
  }, [connected]);

  // ── Snapshot processing ────────────────────────────────────────────────────
  useEffect(() => {
    if (!snapshot) return;

    const pnl = snapshot.broker?.daily_pnl ?? 0;
    const t   = nowTime();
    setPnlHistory((prev) => {
      const next = [...prev, { t, pnl }];
      return next.length > MAX_PNL_HISTORY ? next.slice(-MAX_PNL_HISTORY) : next;
    });

    // Deduplicate signals
    const incoming = snapshot.signals ?? [];
    const freshSignals = [];
    setSignals((prev) => {
      const seen  = seenSignalsRef.current;
      const fresh = incoming.filter((s) => {
        const key = `${s.ts ?? s.t}:${s.symbol}:${s.side}:${s.strategy}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
      freshSignals.push(...fresh);
      if (!fresh.length) return prev;
      const next = [...prev, ...fresh];
      return next.length > MAX_SIGNALS ? next.slice(-MAX_SIGNALS) : next;
    });

    // Push new signals into activity feed
    freshSignals.forEach((s) => {
      const side    = (s.side ?? "").toUpperCase();
      const type    = side === "BUY" ? "signal_buy" : side === "SELL" ? "signal_sell" : "signal_exit";
      const approved = s.approved ? "✓ trade" : "✗ rejected";
      pushActivity({
        type,
        title: `${side} ${s.symbol}`,
        detail: `${s.strategy} · ${approved}${s.entry ? ` @ ₹${Number(s.entry).toFixed(2)}` : ""}`,
        time: nowTime(),
      });
    });

    // Regime change
    const regime = (snapshot.regime ?? "").replace("Regime.", "");
    if (prevRegimeRef.current && prevRegimeRef.current !== regime && regime) {
      notifs.notify("REGIME", `Regime: ${prevRegimeRef.current} → ${regime}`, "info");
      pushActivity({
        type:   "regime_change",
        title:  `Regime → ${regime}`,
        detail: `was ${prevRegimeRef.current}`,
        time:   nowTime(),
      });
    }
    prevRegimeRef.current = regime;

    // Kill switch
    if (snapshot.broker?.kill_switch_active) {
      notifs.notify("RISK", "Kill switch is ACTIVE — trading halted", "error");
    }

    // Tick milestones every 1000 ticks
    const tickTotal = snapshot.system?.tick_total ?? 0;
    if (tickTotal > 0 && Math.floor(tickTotal / 1000) > Math.floor(prevTickTotal.current / 1000)) {
      pushActivity({ type: "tick_milestone", title: `${tickTotal.toLocaleString()} ticks received`, time: nowTime() });
    }
    prevTickTotal.current = tickTotal;

    // New positions opened
    const openPos = snapshot.broker?.open_positions ?? {};
    Object.keys(openPos).forEach((sym) => {
      if (!prevPositions.current[sym]) {
        pushActivity({ type: "trade_open", title: `Position opened: ${sym}`, detail: `qty ${openPos[sym].qty}`, time: nowTime() });
      }
    });
    // Positions closed
    Object.keys(prevPositions.current).forEach((sym) => {
      if (!openPos[sym]) {
        pushActivity({ type: "trade_close", title: `Position closed: ${sym}`, time: nowTime() });
      }
    });
    prevPositions.current = openPos;

  }, [snapshot, notifs]);

  const handleKill = useCallback(() => {
    send({ type: "kill_switch" });
    pushActivity({ type: "system", title: "Kill switch triggered by user", time: nowTime() });
  }, [send]);

  // ── Risk param changes → send to engine ───────────────────────────────────
  useEffect(() => {
    send({ type: "update_risk", params: riskParams });
  }, [riskParams, send]);

  if (!snapshot && !connected) {
    return (
      <div className="min-h-screen bg-gray-950 text-gray-100 flex items-center justify-center flex-col gap-4">
        <p className="text-cyan-400 font-mono text-xl tracking-widest animate-pulse">
          JARVIS TRADING COMMAND CENTER
        </p>
        <p className="text-gray-600 font-mono text-sm animate-pulse">CONNECTING…</p>
      </div>
    );
  }

  const broker        = snapshot?.broker ?? {};
  const killThreshold = broker.kill_threshold ?? (riskParams.killSwitchPct * (broker.capital ?? 10000));

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      <HUD snapshot={snapshot} connected={connected} onKill={handleKill} mode={autonomy.mode} />
      <DrawdownBar dailyPnl={broker.daily_pnl ?? 0} killThreshold={killThreshold} />

      {autonomy.mode === "SEMI_AUTO" && autonomy.pending.length > 0 && (
        <ApprovalBanner
          pending={autonomy.pending}
          onApprove={autonomy.approve}
          onReject={autonomy.reject}
          onSnooze={autonomy.snooze}
          timeoutSecs={autonomy.approvalTimeout}
        />
      )}

      <NavBar active={view} onChange={setView} onSearchOpen={() => setSearchOpen(true)} />

      <main className="flex-1 overflow-hidden min-h-0 flex flex-col">
        {view === "mission" && (
          <MissionControl
            snapshot={snapshot}
            pnlHistory={pnlHistory}
            signals={signals}
            activity={activity}
            connected={connected}
            onSearchOpen={() => setSearchOpen(true)}
          />
        )}
        {view === "arena"   && <div className="flex-1 overflow-auto"><StrategyArena snapshot={snapshot} signals={signals} /></div>}
        {view === "ledger"  && <div className="flex-1 overflow-auto"><TradeLedger /></div>}
        {view === "brain"   && <div className="flex-1 overflow-auto"><BrainAnalytics snapshot={snapshot} pnlHistory={pnlHistory} /></div>}
        {view === "control" && (
          <div className="flex-1 overflow-auto">
            <ControlRoom
              snapshot={snapshot}
              mode={autonomy.mode}
              changeMode={autonomy.changeMode}
              approvalTimeout={autonomy.approvalTimeout}
              setApprovalTimeout={autonomy.setApprovalTimeout}
              riskParams={riskParams}
              setRiskParams={setRiskParams}
              filters={notifs.filters}
              toggleFilter={notifs.toggleFilter}
              onKill={handleKill}
              onSearchOpen={() => setSearchOpen(true)}
            />
          </div>
        )}
        {view === "log"   && <div className="flex-1 overflow-auto"><CommandLog /></div>}
        {view === "chart" && (
          <div className="flex-1 overflow-auto">
            <Suspense fallback={<div className="p-8 text-center text-cyan-600 font-mono text-sm animate-pulse">Loading chart…</div>}>
              <ChartView snapshot={snapshot} />
            </Suspense>
          </div>
        )}
      </main>

      <NotificationStack banners={notifs.banners} onDismiss={notifs.dismiss} />
      <GlobalSearch snapshot={snapshot} open={searchOpen} onClose={() => setSearchOpen(false)} />
    </div>
  );
}
