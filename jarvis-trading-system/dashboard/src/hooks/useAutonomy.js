import { useCallback, useState } from "react";

export const MODES = ["MANUAL", "SEMI_AUTO", "FULL_AUTO"];

export const MODE_LABEL = {
  MANUAL: "MANUAL",
  SEMI_AUTO: "SEMI AUTO",
  FULL_AUTO: "FULL AUTO",
};

export const MODE_STYLE = {
  MANUAL:    "border-gray-600 text-gray-400 bg-gray-800/60",
  SEMI_AUTO: "border-yellow-600 text-yellow-400 bg-yellow-900/40",
  FULL_AUTO: "border-cyan-600 text-cyan-400 bg-cyan-900/40",
};

export default function useAutonomy(send) {
  const [mode, setMode] = useState("SEMI_AUTO");
  const [pending, setPending] = useState([]);
  const [approvalTimeout, setApprovalTimeout] = useState(30);

  const changeMode = useCallback(
    (newMode) => {
      setMode(newMode);
      send({ type: "set_autonomy", mode: newMode });
    },
    [send],
  );

  const addPending = useCallback((item) => {
    setPending((q) => [
      ...q,
      { id: Date.now(), ts: new Date().toISOString(), ...item },
    ]);
  }, []);

  const approve = useCallback(
    (id) => {
      setPending((q) => q.filter((p) => p.id !== id));
      send({ type: "approve", id });
    },
    [send],
  );

  const reject = useCallback(
    (id) => {
      setPending((q) => q.filter((p) => p.id !== id));
      send({ type: "reject", id });
    },
    [send],
  );

  const snooze = useCallback((id) => {
    setPending((q) => {
      const item = q.find((p) => p.id === id);
      if (!item) return q;
      return [...q.filter((p) => p.id !== id), { ...item, snoozed: true }];
    });
  }, []);

  return {
    mode,
    changeMode,
    pending,
    addPending,
    approve,
    reject,
    snooze,
    approvalTimeout,
    setApprovalTimeout,
  };
}
