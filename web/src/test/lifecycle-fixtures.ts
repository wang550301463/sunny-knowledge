export const lifecycleRevision = {
  id: "r1", page_id: "page:p", number: 1, base_revision: null,
  created_by: "alice", created_at: "2026-09-08T00:00:00Z", publication_kind: "reviewed",
  content: { title: "Lifecycle Wiki", markdown: "VISIBLE-WIKI-BODY", entity_type: "File" as const,
    state: "valid" as const, claims: [], evidence: [] },
};
export const lifecyclePage = {
  id: "page:p", space_id: "space", current_revision: "r1", revision_number: 1,
  created_by: "alice", created_at: "2026-09-08T00:00:00Z", revision: lifecycleRevision,
};
export function lifecycleMetrics(page = "page:p", revision = "r1") {
  return {
    page_id: page, revision_id: revision, policy_version: "freshness-v1",
    as_of: "2026-09-08T12:00:00Z",
    support: { registered_source_count: 2, eligible_registered_source_count: 1,
      fact_claim_count: 3, fact_claims_with_original_evidence: 2 },
    validity: { state: "valid", is_current: true, within_valid_time: true,
      claims_valid: true, eligible: true },
    freshness: { policy_version: "freshness-v1", observed_at: "2026-08-18T12:00:00Z",
      half_life_days: 21, age_seconds: 1814400, score: 0.5 },
    access: { scope: "mine_current_acl_domain", mine_visits_7d: 2,
      mine_visits_30d: 7, mine_last_accessed_at: "2026-09-08T11:00:00Z" },
  };
}