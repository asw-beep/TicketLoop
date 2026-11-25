import { useEffect, useState } from "react";
import { serverNow } from "./clock";

/** The server's time, refreshed every `everyMs` while `running`. */
export function useServerClock(running: boolean, everyMs: number): number {
  const [now, setNow] = useState(() => serverNow());
  useEffect(() => {
    if (!running) return;
    const timer = setInterval(() => setNow(serverNow()), everyMs);
    return () => clearInterval(timer);
  }, [running, everyMs]);
  return now;
}
