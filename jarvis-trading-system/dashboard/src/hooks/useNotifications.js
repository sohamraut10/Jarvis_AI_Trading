import { useCallback, useEffect, useRef, useState } from "react";

const DEFAULT_FILTERS = {
  REGIME: true,
  ROTATION: true,
  ENTRY: true,
  EXIT: true,
  SIZING: true,
  BRAIN: true,
  APPROVAL: true,
  RISK: true,
};

export default function useNotifications() {
  const [banners, setBanners] = useState([]);
  const [filters, setFilters] = useState(DEFAULT_FILTERS);
  const pushEnabled = useRef(false);

  useEffect(() => {
    if ("Notification" in window && Notification.permission === "default") {
      Notification.requestPermission().then((p) => {
        pushEnabled.current = p === "granted";
      });
    } else {
      pushEnabled.current = Notification?.permission === "granted";
    }
  }, []);

  const notify = useCallback(
    (category, message, level = "info") => {
      if (!filters[category]) return;

      const id = Date.now() + Math.random();
      setBanners((b) => [
        { id, category, message, level, ts: new Date().toLocaleTimeString() },
        ...b.slice(0, 4),
      ]);
      setTimeout(() => setBanners((b) => b.filter((n) => n.id !== id)), 6000);

      if (pushEnabled.current && (level === "warn" || level === "error")) {
        try {
          new Notification("JARVIS", { body: message, silent: true });
        } catch {
          // silently fail if push not available
        }
      }
    },
    [filters],
  );

  const dismiss = useCallback((id) => {
    setBanners((b) => b.filter((n) => n.id !== id));
  }, []);

  const toggleFilter = useCallback((category) => {
    setFilters((f) => ({ ...f, [category]: !f[category] }));
  }, []);

  return { banners, notify, dismiss, filters, toggleFilter };
}
