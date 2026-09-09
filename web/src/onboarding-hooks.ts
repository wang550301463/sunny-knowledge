import { useCallback, useEffect, useRef, useState } from "react";
import { ApiClient, ApiError } from "./api";
import { loadOnboarding } from "./onboarding-api";
import type { OnboardingSelection, OnboardingSnapshot } from "./onboarding-types";
export const ONBOARDING_READ_TIMEOUT = 10_000;
function deadline<T>(operation: (signal: AbortSignal) => Promise<T>, abort: AbortController): Promise<T> {
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (success: boolean, value: unknown) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      abort.signal.removeEventListener("abort", cancelled);
      if (success) resolve(value as T); else reject(value);
    };
    const cancelled = () => finish(false, new DOMException("Read cancelled", "AbortError"));
    const timer = window.setTimeout(() => { finish(false, new ApiError(504, "onboarding_read_timeout")); abort.abort(); }, ONBOARDING_READ_TIMEOUT);
    abort.signal.addEventListener("abort", cancelled, { once: true });
    if (abort.signal.aborted) { cancelled(); return; }
    try { operation(abort.signal).then(value => finish(true, value), error => finish(false, error)); }
    catch (error) { finish(false, error); }
  });
}
export function useOnboarding(api: ApiClient, selection: OnboardingSelection) {
  const key = JSON.stringify(selection), chosen = useRef(selection);
  chosen.current = selection;
  const serial = useRef(0), live = useRef(false), active = useRef(false);
  const pending = useRef<Promise<void>|undefined>(undefined), controller = useRef<AbortController|undefined>(undefined);
  const [state, setState] = useState<{ key: string; data?: OnboardingSnapshot; loading: boolean; suspended: boolean; error?: unknown }>({ key, loading: true, suspended: false });
  const read = useCallback((clear = false): Promise<void> => {
    if (!live.current || !active.current) return Promise.resolve();
    if (pending.current && !clear) return pending.current;
    controller.current?.abort();
    const ticket = ++serial.current, abort = new AbortController();
    controller.current = abort;
    setState(before => clear ? { key, loading: true, suspended: false } : { ...before, loading: true });
    const work = (async () => {
      try {
        const data = await deadline(signal => loadOnboarding(api, chosen.current, signal), abort);
        if (live.current && active.current && ticket === serial.current) setState({ key, data, loading: false, suspended: false });
      } catch (error) {
        if (live.current && ticket === serial.current) {
          serial.current++;
          abort.abort(); pending.current = undefined;
          setState({ key, loading: false, suspended: !active.current, error });
        }
      } finally { if (ticket === serial.current) pending.current = undefined; }
    })();
    pending.current = work;
    return work;
  }, [api, key]);
  useEffect(() => {
    live.current = true;
    const hide = () => {
      active.current = false; serial.current++; controller.current?.abort(); pending.current = undefined;
      setState({ key, loading: false, suspended: true });
    };
    const focus = () => {
      if (document.visibilityState === "hidden") { hide(); return; }
      active.current = true;
      void read(true);
    };
    const visibility = () => {
      if (document.visibilityState === "hidden" || !document.hasFocus()) hide(); else focus();
    };
    active.current = document.visibilityState !== "hidden" && document.hasFocus();
    if (active.current) void read(true); else hide();
    const timer = window.setInterval(() => { if (active.current && document.visibilityState !== "hidden") void read(); }, 5000);
    window.addEventListener("focus", focus); window.addEventListener("blur", hide);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      live.current = false; active.current = false; serial.current++; controller.current?.abort(); pending.current = undefined;
      window.clearInterval(timer); window.removeEventListener("focus", focus); window.removeEventListener("blur", hide);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [key, read]);
  return { ...(state.key === key ? state : { key, loading: true, suspended: false }), refresh: () => { void read(true); } };
}