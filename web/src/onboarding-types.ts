export interface OnboardingSelection { space: string; source: string; page: string; session: string; run: string }
export interface OnboardingChoice { id: string; name: string }
export interface OnboardingModel extends OnboardingChoice { configuration: string; capability: "chat" | "embedding" | "rerank"; tested: boolean; simulated: boolean }
export interface OnboardingSnapshot {
  actor: string; epoch: number; admin: boolean; checkedAt: number;
  chatModels: OnboardingModel[]; adminModels?: OnboardingModel[];
  retrievalVerification: "unknown";
  spaces: OnboardingChoice[]; spacesMore: boolean; space?: OnboardingChoice;
  sources: OnboardingChoice[]; sourcesMore: boolean;
  source?: OnboardingChoice & { version: number; active: boolean; previewRevision: string | null; fileCount: number; latestTask: string | null };
  task?: { id: string; status: string; currentVersion: boolean; error: string | null; pages: OnboardingChoice[] };
  page?: OnboardingChoice & { revision: string; supported: boolean; referenceCount: number };
  sessions: OnboardingChoice[]; sessionsMore: boolean;
  runs: OnboardingChoice[]; runsMore: boolean;
  run?: { id: string; status: string; hidden: boolean; verified: boolean; citationCount: number };
}