export interface LifecycleMetrics {
  page_id: string;
  revision_id: string;
  policy_version: "freshness-v1";
  as_of: string;
  support: {
    registered_source_count: number;
    eligible_registered_source_count: number;
    fact_claim_count: number;
    fact_claims_with_original_evidence: number;
  };
  validity: {
    state: "valid" | "stale" | "retracted";
    is_current: boolean;
    within_valid_time: boolean;
    claims_valid: boolean;
    eligible: boolean;
  };
  freshness: {
    policy_version: "freshness-v1";
    observed_at: string | null;
    half_life_days: number;
    age_seconds: number | null;
    score: number | null;
  };
  access: {
    scope: "mine_current_acl_domain";
    mine_visits_7d: number;
    mine_visits_30d: number;
    mine_last_accessed_at: string | null;
  };
}