import { useCallback, useEffect, useState } from "react";

/** The in-app Replicate On / Off switch. Stored on the server, so no restart is needed. */
const EVENT = "flyerz-replicate-switch";

export function useReplicateSwitch() {
  const [enabled, setEnabled] = useState<boolean | null>(null);

  useEffect(() => {
    let live = true;
    const load = () =>
      fetch("/api/replicate/settings", { credentials: "include" })
        .then((res) => res.json())
        .then((body: { enabled?: boolean }) => { if (live) setEnabled(body.enabled !== false); })
        .catch(() => { if (live) setEnabled(true); });
    load();
    window.addEventListener(EVENT, load);
    return () => { live = false; window.removeEventListener(EVENT, load); };
  }, []);

  const set = useCallback(async (next: boolean) => {
    setEnabled(next);
    try {
      const res = await fetch("/api/replicate/settings", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled: next }),
      });
      const body = (await res.json()) as { enabled?: boolean };
      setEnabled(body.enabled !== false);
      window.dispatchEvent(new Event(EVENT));
    } catch {
      setEnabled(!next);
    }
  }, []);

  return { enabled, set };
}

export const REPLICATE_OFF_NOTE = "Replicate is switched off in settings";
