import type { User } from "oidc-client-ts";
import { ApiClient, ApiError, pathId } from "./api";

export function lifecycleActor(user: User | null | undefined): string | null {
  if (!user?.profile?.sub || !user.profile.iss) return null;
  return JSON.stringify([user.profile.iss, user.profile.sub, user.profile.sid ?? user.session_state ?? user.profile.auth_time ?? ""]);
}
interface Receipt { id: string; recorded: boolean }
export class DisplayStorageUnavailable extends Error {}
export async function displayReceipt(actor: string, entry: string, page: string, revision: string) {
  try {
    const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify([actor, entry, page, revision])));
    const key = "knowledge.lifecycle.display." + Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2, "0")).join("");
    const saved = sessionStorage.getItem(key);
    const receipt = saved ? JSON.parse(saved) as Receipt : { id: "web-display:" + crypto.randomUUID(), recorded: false };
    if (typeof receipt.id !== "string" || !/^web-display:[a-f0-9-]{36}$/.test(receipt.id) || typeof receipt.recorded !== "boolean") throw new Error();
    if (!saved) sessionStorage.setItem(key, JSON.stringify(receipt));
    return { receipt, mark: () => { sessionStorage.setItem(key, JSON.stringify({ ...receipt, recorded: true })); } };
  } catch { throw new DisplayStorageUnavailable("Display receipt cannot be retained"); }
}
export async function recordDisplay(api: ApiClient, page: string, revision: string, id: string, signal: AbortSignal) {
  const value = await api.request<{ id: string; revision_id: string; recorded_at: string }>(`/pages/${pathId(page)}/access-events`, {
    method: "POST", body: JSON.stringify({ revision_id: revision, idempotency_key: id }), signal,
  });
  if (!value || typeof value.id !== "string" || !value.id || value.revision_id !== revision || typeof value.recorded_at !== "string" || !Number.isFinite(Date.parse(value.recorded_at))) throw new ApiError(502, "invalid_access_receipt");
}