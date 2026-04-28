import { useCallback, useEffect, useRef, useState } from "react";
import ApprovalBanner from "./components/ApprovalBanner";
import DrawdownBar from "./components/DrawdownBar";
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

const WS_URL =
  import.meta.env.VITE_WS_URL ??
  `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ws`;

const MAX_PNL_HISTORY = 300;
const MAX_SIGNALS = 100;

const DEFAULT_RISK = {
  killSwitchPct:   0.03,
  kellyFraction:   0.50,
  maxTradePct:     0.20,
  maxConcentration: 0.30,
};

export default function App() {
  const { snapshot, connected, send } = useWebSocket(WS_URL);
  const autonomy = useAutonomy(send);
  const notifs   = useNotifications();

  const [view, setView]           = useState("mission");
  const [pnlHistory, setPnlHistory] = useState([]);
  const [signals, setSignals]     = useState([]);
  const [riskParams, setRiskParams] = useState(DEFAULT_RISK);
  const seenSignalsRef = useRef(new Set());
  const prevRegimeRef  = useRef(null);

  // ── Snapshot processing ────────────────────────────────────────────────────
  useEffect(() => {
    if (!snapshot) return;

    const pnl = snapshot.broker?.daily_pnl ?? 0;
    const t   = new Date().toLocaleTimeString("en-IN", { hour12: false });
    setPnlHistory((prev) => {
      const next = [...prev, { t, pnl }];
      return next.length > MAX_PNL_HISTORY ? next.slice(-MAX_PNL_HISTORY) : next;
    });

    // Deduplicate signals (server uses "signals" key)
    const incoming = snapshot.signals ?? [];
    setSignals((prev) => {
      const seen = seenSignalsRef.current;
      const fresh = incoming.filter((s) => {
        const key = `${s.t}:${s.symbol}:${s.side}:${s.strategy}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      });
      if (!fresh.length) return prev;
      const next = [...prev, ...fresh];
      return next.length > MAX_SIGNALS ? next.slice(-MAX_SIGNALS) : next;
    });

    // Notify on regime change
    const regime = (snapshot.regime ?? "").replace("Regime.", "");
    if (prevRegimeRef.current && prevRegimeRef.current !== regime) {
      notifs.notify("REGIME", `Regime changed: ${prevRegimeRef.current} → ${regime}`, "info");
    }
    prevRegimeRef.current = regime;

    // Notify on kill-switch
    if (snapshot.broker?.kill_switch_active) {
      notifs.notify("RISK", "Kill switch is ACTIVE — trading halted", "error");
    }
  }, [snapshot, notifs]);

  const handleKill = useCallback(() => {
    send({ type: "kill_switch" });
  }, [send]);

  // ── Risk param changes → send to engine ───────────────────────────────────
  useEffect(() => {
    send({ type: "update_risk", params: riskParams });
  }, [riskParams, send]);

  if (!snapshot && !connected) {
    return (
      <div className="min-h-screen bg-gray-950 text-gray-100 flex items-center justify-center">
        <p className="text-cyan-400 font-mono text-xl tracking-widest animate-pulse">
          JARVIS TRADING COMMAND CENTER — CONNECTING…
        </p>
      </div>
    );
  }

  const broker       = snapshot?.broker ?? {};
  const killThreshold = broker.kill_threshold ?? (riskParams.killSwitchPct * (broker.capital ?? 10000));

  return (
    <div className="min-h-screen bg-gray-950 text-gray-100 flex flex-col">
      <HUD
        snapshot={snapshot}
        connected={connected}
        onKill={handleKill}
        mode={autonomy.mode}
      />

      <DrawdownBar
        dailyPnl={broker.daily_pnl ?? 0}
        killThreshold={killThreshold}
      />

      {autonomy.mode === "SEMI_AUTO" && autonomy.pending.length > 0 && (
        <ApprovalBanner
          pending={autonomy.pending}
          onApprove={autonomy.approve}
          onReject={autonomy.reject}
          onSnooze={autonomy.snooze}
          timeoutSecs={autonomy.approvalTimeout}
        />
      )}

      <NavBar active={view} onChange={setView} />

      <main className="flex-1 overflow-auto min-h-0">
        {view === "mission" && (
          <MissionControl
            snapshot={snapshot}
            pnlHistory={pnlHistory}
            signals={signals}
            connected={connected}
          />
        )}
        {view === "arena" && (
          <StrategyArena snapshot={snapshot} signals={signals} />
        )}
        {view === "ledger" && <TradeLedger />}
        {view === "brain"  && (
          <BrainAnalytics snapshot={snapshot} pnlHistory={pnlHistory} />
        )}
        {view === "control" && (
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
          />
        )}
        {view === "log" && <CommandLog />}
      </main>

      <NotificationStack banners={notifs.banners} onDismiss={notifs.dismiss} />
    </div>
  );
}
