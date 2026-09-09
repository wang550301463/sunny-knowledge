import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, pathId } from "./api";
import type { ApiClient } from "./api";
import type { Space } from "./types";
import { graphDeadline } from "./graph-deadline";
/** Graph-only metadata boundary; ordinary Wiki/source tabs keep their existing lifecycle. */
export function useGraphSpace(api: ApiClient, spaceId: string) {
  const [state, setState] = useState<{ data?: Space; loading: boolean; error?: unknown }>({ loading: true });
  const live = useRef(true), serial = useRef(0), pending = useRef<Promise<void> | undefined>(undefined), controller = useRef<AbortController | undefined>(undefined);
  const read = useCallback((clear = false): Promise<void> => {
    if (!live.current) return Promise.resolve();
    if (pending.current && !clear) return pending.current;
    controller.current?.abort();
    const generation = ++serial.current, abort = new AbortController(); controller.current = abort;
    setState(s => clear ? { loading: true } : { ...s, loading: true });
    const work = (async () => {
      try {
        const value = await graphDeadline(signal => api.get<Space>(`/spaces/${pathId(spaceId)}`, signal), abort);
        if (!live.current || generation !== serial.current) return;
        if (!value || value.id !== spaceId || typeof value.name !== "string") throw new ApiError(502, "invalid_space_response");
        setState({ data: { id: value.id, name: value.name }, loading: false });
      } catch (error) {
        if (live.current && generation === serial.current) {
          serial.current += 1; pending.current = undefined; abort.abort();
          setState({ loading: false, error });
        }
      } finally { if (generation === serial.current) pending.current = undefined; }
    })();
    pending.current = work;
    return work;
  }, [api, spaceId]);
  useEffect(() => {
    live.current = true; void read(true);
    const focus = () => { if (document.visibilityState !== "hidden") void read(true); };
    const timer = window.setInterval(() => { if (document.visibilityState !== "hidden") void read(); }, 5000);
    window.addEventListener("focus", focus); document.addEventListener("visibilitychange", focus);
    return () => { live.current = false; serial.current += 1; controller.current?.abort(); pending.current = undefined; window.clearInterval(timer); window.removeEventListener("focus", focus); document.removeEventListener("visibilitychange", focus); };
  }, [read]);
  return { ...state, refresh: () => { void read(true); } };
}