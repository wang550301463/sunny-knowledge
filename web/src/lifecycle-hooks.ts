import { useCallback, useEffect, useRef, useState } from "react";
import { LIFECYCLE_POLL_MS, withLifecycleDeadline } from "./lifecycle-api";

export function useLifecycleWikiResource<T>(key: string, loader: (signal: AbortSignal) => Promise<T>, onClear: () => void) {
  const latest = useRef({ loader, onClear }); latest.current = { loader, onClear };
  const [state, setState] = useState<{ key: string; data?: T; error?: unknown; loading: boolean }>({ key, loading: true });
  const actions = useRef({ refresh: () => {}, revalidate: () => {}, invalidate: (_error: unknown) => {} });
  useEffect(() => {
    let active = true, sequence = 0, pending = false, stopped = false;
    let controller = new AbortController();
    const clear = () => { latest.current.onClear(); setState({ key, loading: document.visibilityState === "visible" }); };
    const invalidate = (error: unknown) => {
      sequence++; controller.abort(); pending = false; stopped = true;
      latest.current.onClear(); setState({ key, loading: false, error });
    };
    const load = async (force: boolean) => {
      if (!active || document.visibilityState !== "visible" || (!force && (pending || stopped))) return;
      if (force) { controller.abort(); clear(); stopped = false; }
      const ticket = ++sequence; pending = true; controller = new AbortController();
      try {
        const data = await withLifecycleDeadline(latest.current.loader, controller.signal);
        if (active && ticket === sequence) setState({ key, data, loading: false });
      } catch (error) {
        if (active && ticket === sequence) invalidate(error);
      } finally { if (ticket === sequence) pending = false; }
    };
    const focus = () => { void load(true); };
    const visibility = () => {
      sequence++; controller.abort(); pending = false; clear();
      if (document.visibilityState === "visible") void load(true);
    };
    actions.current = { refresh: focus, revalidate: () => { void load(false); }, invalidate };
    clear(); void load(true);
    const timer = window.setInterval(() => { void load(false); }, LIFECYCLE_POLL_MS);
    window.addEventListener("focus", focus); document.addEventListener("visibilitychange", visibility);
    return () => { active = false; sequence++; controller.abort(); window.clearInterval(timer); window.removeEventListener("focus", focus); document.removeEventListener("visibilitychange", visibility); };
  }, [key]);
  return {
    ...(state.key === key ? state : { key, loading: true, data: undefined, error: undefined }),
    refresh: useCallback(() => actions.current.refresh(), []),
    revalidate: useCallback(() => actions.current.revalidate(), []),
    invalidate: useCallback((error: unknown) => actions.current.invalidate(error), []),
  };
}