import { ApiError } from "./api";
/** One total client-side deadline, independent of polling and transport cancellation support. */
export const GRAPH_READ_DEADLINE_MS = 30_000;
export function graphDeadline<T>(operation: (signal: AbortSignal) => Promise<T>, controller: AbortController): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    let settled = false;
    const finish = (value: T | undefined, error?: unknown) => {
      if (settled) return;
      settled = true;
      window.clearTimeout(timer);
      controller.signal.removeEventListener("abort", aborted);
      if (error !== undefined) reject(error); else resolve(value as T);
    };
    const aborted = () => finish(undefined, new DOMException("Read cancelled", "AbortError"));
    const timer = window.setTimeout(() => {
      finish(undefined, new ApiError(504, "graph_read_timeout"));
      controller.abort();
    }, GRAPH_READ_DEADLINE_MS);
    if (controller.signal.aborted) { aborted(); return; }
    controller.signal.addEventListener("abort", aborted, { once: true });
    try { operation(controller.signal).then(value => finish(value), error => finish(undefined, error)); }
    catch (error) { finish(undefined, error); }
  });
}