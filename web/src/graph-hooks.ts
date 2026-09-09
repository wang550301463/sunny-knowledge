import { useCallback, useEffect, useRef, useState } from "react";
import type { ApiClient } from "./api";
import { decodeGraphResult } from "./graph-api";
import type { GraphExecution, GraphParameters, GraphResult } from "./graph-types";
interface State { data?: GraphResult; execution?: GraphExecution; loading: boolean; error?: unknown; fence: number }
/** Search is explicit/billable. Every passive read and evidence check uses only traverse. */
export function useGraphExplorer(api: ApiClient) {
  const [state, setState] = useState<State>({ loading: false, fence: 0 });
  const command = useRef<GraphExecution | undefined>(undefined);
  const current = useRef<GraphResult | undefined>(undefined);
  const serial = useRef(0), live = useRef(true);
  const controller = useRef<AbortController | undefined>(undefined);
  const pending = useRef<Promise<GraphResult | undefined> | undefined>(undefined);
  const invalidate = useCallback((error: unknown) => {
    serial.current++; controller.current?.abort(); pending.current = undefined; current.current = undefined;
    setState(s => ({ execution: s.execution, loading: false, error, fence: s.fence + 1 }));
  }, []);
  const read = useCallback((clear = false, initialSearch = false): Promise<GraphResult | undefined> => {
    const cmd = command.current;
    if (!cmd || !live.current) return Promise.resolve(undefined);
    if (pending.current && !clear) return pending.current;
    controller.current?.abort();
    const generation = ++serial.current, abort = new AbortController(); controller.current = abort;
    if (clear) { current.current = undefined; setState(s => ({ execution: cmd, loading: true, fence: s.fence + 1 })); }
    else setState(s => ({ ...s, loading: true, error: undefined }));
    const work = (async () => {
      try {
        // An empty search has no authorized seed to revalidate and never repeats a model call.
        if (!initialSearch && !cmd.seed_fragment_ids.length) {
          const empty = current.current;
          setState(s => ({ ...s, loading: false }));
          return empty;
        }
        const { relation, ...scope } = cmd.params;
        const body = initialSearch ? { ...scope, query: cmd.query, limit: 12 } : { ...scope, relation, seed_fragment_ids: cmd.seed_fragment_ids };
        const raw = await api.request<unknown>(initialSearch ? "/search" : "/traverse", { method: "POST", body: JSON.stringify(body), signal: abort.signal });
        if (!live.current || generation !== serial.current) return undefined;
        let data = decodeGraphResult(raw);
        if (cmd.mode === "search") {
          if (!initialSearch) {
            const items = data.items.filter(i => i.primary && cmd.seed_fragment_ids.includes(i.id)), allowed = new Set(items.flatMap(i => i.citation_ids));
            data = { ...data, items, evidence: data.evidence.filter(e => allowed.has(e.id)), graph: { nodes: [], edges: [], paths: [], degraded: [] } };
          }
          cmd.seed_fragment_ids = data.items.map(i => i.id).slice(0, 30);
        }
        current.current = data;
        setState(s => ({ data, execution: { ...cmd, seed_fragment_ids: [...cmd.seed_fragment_ids] }, loading: false, fence: s.fence }));
        return data;
      } catch (error) {
        if (live.current && generation === serial.current) { current.current = undefined; setState(s => ({ execution: cmd, loading: false, error, fence: s.fence + 1 })); }
        return undefined;
      } finally { if (generation === serial.current) pending.current = undefined; }
    })();
    pending.current = work;
    return work;
  }, [api]);
  const refresh = useCallback((clear = false) => read(clear), [read]);
  const start = useCallback((execution: GraphExecution) => {
    command.current = structuredClone(execution);
    return read(true, execution.mode === "search");
  }, [read]);
  const search = useCallback((query: string, params: GraphParameters) => start({ mode: "search", query, params, seed_fragment_ids: [] }), [start]);
  const traverse = useCallback((seeds: string[], params: GraphParameters) => start({ mode: "graph", query: "", params, seed_fragment_ids: seeds }), [start]);
  useEffect(() => {
    live.current = true;
    const focus = () => { if (document.visibilityState !== "hidden") void read(true); };
    const timer = window.setInterval(() => { if (document.visibilityState !== "hidden") void read(false); }, 5000);
    window.addEventListener("focus", focus); document.addEventListener("visibilitychange", focus);
    return () => { live.current = false; serial.current++; controller.current?.abort(); pending.current = undefined; window.clearInterval(timer); window.removeEventListener("focus", focus); document.removeEventListener("visibilitychange", focus); };
  }, [read]);
  return { ...state, search, traverse, refresh, invalidate };
}