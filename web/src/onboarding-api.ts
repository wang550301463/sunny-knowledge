import { ApiClient, ApiError, pathId, query } from "./api";
import { readableRun } from "./agent-api";
import type { OnboardingChoice, OnboardingModel, OnboardingSelection, OnboardingSnapshot } from "./onboarding-types";
export const emptySelection: OnboardingSelection = { space: "", source: "", page: "", session: "", run: "" };
export function readSelection(value: unknown): OnboardingSelection {
  const input = typeof value === "object" && value !== null ? value as Record<string, unknown> : {};
  return Object.fromEntries(Object.keys(emptySelection).map(key => [key, typeof input[key] === "string" && input[key].length <= 512 && !/[\u0000-\u001f]/.test(input[key]) ? input[key] : ""])) as unknown as OnboardingSelection;
}
function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new ApiError(502, "invalid_onboarding_response");
  return value as Record<string, unknown>;
}
function text(value: unknown, max = 512): string {
  if (typeof value !== "string" || value.length > max) throw new ApiError(502, "invalid_onboarding_response");
  return value;
}
function array(value: unknown, max = 1000): unknown[] {
  if (!Array.isArray(value) || value.length > max) throw new ApiError(502, "invalid_onboarding_response");
  return value;
}
function number(value: unknown): number {
  if (!Number.isSafeInteger(value) || (value as number) < 0) throw new ApiError(502, "invalid_onboarding_response");
  return value as number;
}
function choice(value: unknown, label = "name"): OnboardingChoice {
  const row = object(value);
  return { id: text(row.id), name: text(row[label], 1000) };
}
function model(value: unknown): OnboardingModel {
  const row = object(value), capability = text(row.capability), capabilities = object(row.capabilities);
  if (!["chat", "embedding", "rerank"].includes(capability)) throw new ApiError(502, "invalid_onboarding_model");
  const provider = text(row.provider_model, 1000);
  return { ...choice(row), configuration: text(row.configuration_id), capability: capability as OnboardingModel["capability"],
    tested: row.state === "active" && row.test_state === "passed" && capabilities[capability] === true && (capability !== "chat" || capabilities.tools === true && capabilities.stream === true),
    simulated: provider.startsWith("protocol-fixture-") };
}
async function list(api: ApiClient, path: string, signal: AbortSignal, args: Record<string, string> = {}) {
  const raw = object(await api.get(path + query({ ...args, limit: 50 }), signal));
  return { items: array(raw.items, 50), more: raw.next_cursor !== undefined && raw.next_cursor !== null };
}
function matches(row: Record<string, unknown>, fields: Record<string, string>) {
  if (Object.entries(fields).some(([key, value]) => row[key] !== value)) throw new ApiError(502, "onboarding_identity_mismatch");
}
export async function loadOnboarding(api: ApiClient, selection: OnboardingSelection, signal: AbortSignal): Promise<OnboardingSnapshot> {
  const me = object(await api.get("/me", signal)), actor = text(me.id), epoch = number(me.auth_epoch);
  const admin = array(me.permissions).includes("platform_admin");
  const initial = await Promise.all([
    list(api, "/agents/models", signal), list(api, "/spaces", signal), list(api, "/sessions", signal),
    admin ? list(api, "/models", signal) : Promise.resolve(undefined),
  ]);
  const data: OnboardingSnapshot = { actor, epoch, admin, checkedAt: Date.now(), retrievalVerification: "unknown",
    chatModels: initial[0].items.map(model), adminModels: initial[3]?.items.map(model),
    spaces: initial[1].items.map(value => choice(value)), spacesMore: initial[1].more,
    sessions: initial[2].items.map(value => choice(value, "title")), sessionsMore: initial[2].more,
    sources: [], sourcesMore: false, runs: [], runsMore: false };
  if (selection.space) {
    const space = object(await api.get(`/spaces/${pathId(selection.space)}`, signal));
    matches(space, { id: selection.space });
    data.space = choice(space);
    const sources = await list(api, "/sources", signal, { space_id: selection.space });
    data.sources = sources.items.map(value => { const item = object(value); matches(item, { space_id: selection.space }); return choice(item); });
    data.sourcesMore = sources.more;
  }
  if (selection.source && data.space) {
    const source = object(await api.get(`/sources/${pathId(selection.source)}`, signal));
    matches(source, { id: selection.source, space_id: selection.space });
    const preview = source.preview === null ? null : object(source.preview);
    data.source = { ...choice(source), version: number(source.version), active: source.state === "active",
      previewRevision: preview ? text(preview.source_revision) : null, fileCount: preview ? number(preview.file_count) : 0,
      latestTask: source.latest_task_id === null ? null : text(source.latest_task_id) };
    if (data.source.latestTask) {
      const task = object(await api.get(`/tasks/${pathId(data.source.latestTask)}`, signal));
      matches(task, { id: data.source.latestTask, source_id: selection.source, space_id: selection.space });
      const items = object(task.result).items;
      const pages = items === undefined ? [] : array(items).flatMap(value => {
        const item = object(value);
        return typeof item.page_id === "string" ? [{ id: text(item.page_id), name: typeof item.path === "string" ? text(item.path, 1000) : text(item.page_id) }] : [];
      });
      data.task = { id: text(task.id), status: text(task.status), currentVersion: task.source_version === data.source.version && task.operation === "sync",
        error: typeof task.error_code === "string" ? text(task.error_code) : null,
        pages: [...new Map(pages.map(page => [page.id, page])).values()] };
    }
  }
  if (selection.page && data.source) {
    const page = object(await api.get(`/pages/${pathId(selection.page)}`, signal));
    matches(page, { id: selection.page, space_id: selection.space });
    if (page.revision !== null) {
      const revision = object(page.revision), content = object(revision.content);
      matches(revision, { id: text(page.current_revision), page_id: selection.page });
      const refs = [...array(content.evidence), ...array(content.claims).flatMap(value => array(object(value).evidence))].map(object);
      const supported = refs.some(ref => ref.source_id === selection.source && ref.source_revision === data.source?.previewRevision);
      data.page = { id: text(page.id), name: text(content.title, 1000), revision: text(revision.id),
        supported: content.state === "valid" && data.source.active && supported,
        referenceCount: new Set(refs.map(ref => JSON.stringify([ref.source_id, ref.revision_id, ref.path, ref.start_line, ref.end_line]))).size };
    }
  }
  if (selection.session) {
    const history = await list(api, `/sessions/${pathId(selection.session)}/history`, signal);
    data.runs = history.items.flatMap(value => {
      const run = object(value);
      matches(run, { session_id: selection.session });
      return run.content_hidden === false ? [{ id: text(run.id), name: `${text(run.created_at)} · ${text(run.status)}` }] : [];
    });
    data.runsMore = history.more;
  }
  if (selection.run && selection.session) {
    const run = readableRun(await api.get(`/runs/${pathId(selection.run)}`, signal));
    matches(run as unknown as Record<string, unknown>, { id: selection.run, session_id: selection.session });
    let citationCount = 0;
    if (!run.content_hidden && run.entrypoint !== "channel" && data.page?.supported && run.actual_scope.includes(selection.space)) {
      const referenced = new Set([...(run.answer?.facts ?? []), ...(run.answer?.inferences ?? [])].filter(claim => claim.text.trim()).flatMap(claim => claim.citation_ids));
      citationCount = new Set(run.citations.filter(citation => referenced.has(citation.id) && citation.page_id === selection.page && citation.revision_id === data.page?.revision && citation.evidence.source_id === selection.source && citation.evidence.source_revision === data.source?.previewRevision).map(citation => citation.id)).size;
    }
    data.run = { id: run.id, status: run.status, hidden: run.content_hidden,
      verified: !run.content_hidden && run.status === "completed" && run.answer_complete === true && citationCount > 0,
      citationCount };
  }
  const final = object(await api.get("/me", signal));
  if (final.id !== actor || final.auth_epoch !== epoch) throw new ApiError(409, "authorization_changed");
  return data;
}
export function onboardingChecks(data?: OnboardingSnapshot) {
  return { chat: data?.chatModels.some(model => model.tested) ?? false, space: !!data?.space,
    preview: !!data?.source?.active && !!data.source.previewRevision && data.source.fileCount > 0,
    sync: !!data?.task?.currentVersion && data.task.status === "succeeded",
    wiki: data?.page?.supported ?? false, answer: data?.run?.verified ?? false };
}