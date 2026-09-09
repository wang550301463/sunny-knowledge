import { ApiClient, ApiError, pathId, query } from "./api";
import type { Revision, WikiPage } from "./types";
import type { LifecycleMetrics } from "./lifecycle-types";

export const LIFECYCLE_DEADLINE_MS = 10000;
export const LIFECYCLE_POLL_MS = 5000;
const invalid = () => new ApiError(502, "invalid_lifecycle_response");
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw invalid();
  return value as Record<string, unknown>;
}
function count(value: unknown): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 0) throw invalid();
  return value;
}
function bool(value: unknown): boolean {
  if (typeof value !== "boolean") throw invalid();
  return value;
}
function timestamp(value: unknown): string {
  if (typeof value !== "string" || value.length > 64 || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) throw invalid();
  return value;
}
function optionalTime(value: unknown): string | null { return value === null ? null : timestamp(value); }
function nonnegative(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) throw invalid();
  return value;
}
export function parseLifecycle(raw: unknown, pageId: string, revisionId: string): LifecycleMetrics {
  const value = object(raw), support = object(value.support), validity = object(value.validity), freshness = object(value.freshness), access = object(value.access);
  if (value.page_id !== pageId || value.revision_id !== revisionId || value.policy_version !== "freshness-v1" || freshness.policy_version !== "freshness-v1" || access.scope !== "mine_current_acl_domain") throw invalid();
  if (!["valid", "stale", "retracted"].includes(String(validity.state))) throw invalid();
  const total = count(support.registered_source_count), eligible = count(support.eligible_registered_source_count), facts = count(support.fact_claim_count), supported = count(support.fact_claims_with_original_evidence);
  const seven = count(access.mine_visits_7d), thirty = count(access.mine_visits_30d);
  const halfLife = count(freshness.half_life_days), score = freshness.score === null ? null : nonnegative(freshness.score), age = freshness.age_seconds === null ? null : nonnegative(freshness.age_seconds);
  const observed = optionalTime(freshness.observed_at);
  if (eligible > total || supported > facts || seven > thirty || ![21, 90, 180].includes(halfLife) || (score !== null && (score > 1 || age === null || observed === null)) || (score === null && age !== null)) throw invalid();
  return {
    page_id: pageId, revision_id: revisionId, policy_version: "freshness-v1", as_of: timestamp(value.as_of),
    support: { registered_source_count: total, eligible_registered_source_count: eligible, fact_claim_count: facts, fact_claims_with_original_evidence: supported },
    validity: { state: validity.state as LifecycleMetrics["validity"]["state"], is_current: bool(validity.is_current), within_valid_time: bool(validity.within_valid_time), claims_valid: bool(validity.claims_valid), eligible: bool(validity.eligible) },
    freshness: { policy_version: "freshness-v1", observed_at: observed, half_life_days: halfLife, age_seconds: age, score },
    access: { scope: "mine_current_acl_domain", mine_visits_7d: seven, mine_visits_30d: thirty, mine_last_accessed_at: optionalTime(access.mine_last_accessed_at) },
  };
}
export async function readLifecycle(api: ApiClient, pageId: string, revisionId: string, signal: AbortSignal) {
  return parseLifecycle(await api.get<unknown>(`/pages/${pathId(pageId)}/lifecycle${query({ revision_id: revisionId })}`, signal), pageId, revisionId);
}
export async function readLifecycleWiki(api: ApiClient, pageId: string, signal: AbortSignal) {
  const page = await api.get<WikiPage>(`/pages/${pathId(pageId)}`, signal);
  if (!page || page.id !== pageId || (page.revision && (page.revision.page_id !== pageId || page.revision.id !== page.current_revision))) throw new ApiError(502, "revision_mismatch");
  const metrics = page.revision ? await readLifecycle(api, pageId, page.revision.id, signal) : undefined;
  return { page, metrics };
}
export async function readLifecycleRevision(api: ApiClient, pageId: string, revisionId: string, signal: AbortSignal) {
  const revision = await api.get<Revision>(`/pages/${pathId(pageId)}/revisions/${pathId(revisionId)}`, signal);
  if (!revision || revision.id !== revisionId || revision.page_id !== pageId) throw new ApiError(502, "revision_mismatch");
  return { revision, metrics: await readLifecycle(api, pageId, revisionId, signal) };
}
/** Promise deadlines also fence loaders that ignore AbortSignal, including token acquisition. */
export function withLifecycleDeadline<T>(load: (signal: AbortSignal) => Promise<T>, outer: AbortSignal): Promise<T> {
  return new Promise((resolve, reject) => {
    const controller = new AbortController();
    let settled = false;
    const cleanup = () => { window.clearTimeout(timer); outer.removeEventListener("abort", abort); };
    const finish = (operation: () => void) => { if (settled) return; settled = true; cleanup(); operation(); };
    const abort = () => { controller.abort(); finish(() => reject(new DOMException("Cancelled", "AbortError"))); };
    const timer = window.setTimeout(() => { controller.abort(); finish(() => reject(new ApiError(0, "lifecycle_timeout"))); }, LIFECYCLE_DEADLINE_MS);
    outer.addEventListener("abort", abort, { once: true });
    if (outer.aborted) { abort(); return; }
    void Promise.resolve().then(() => load(controller.signal)).then(value => finish(() => resolve(value)), error => finish(() => reject(error)));
  });
}