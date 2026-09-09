import { ConfigProvider } from "antd";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { OnboardingPage } from "../pages/Onboarding";
import { loadOnboarding, onboardingChecks } from "../onboarding-api";
import { onboardingAPI, onboardingResponses, onboardingSelection } from "./onboarding-fixtures";

const { identity, api } = vi.hoisted(() => ({
  identity: { user: { profile: { iss: "issuer", sub: "person", sid: "session-one" } } },
  api: { get: vi.fn() },
}));
vi.mock("../auth", () => ({ useAuth: () => ({ api, user: identity.user }) }));
beforeEach(() => {
  vi.resetAllMocks();
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  identity.user = { profile: { iss: "issuer", sub: "person", sid: "session-one" } };
});

it("withdraws old actor metadata synchronously when the authentication session changes while the API object stays stable", async () => {
  const source = onboardingAPI();
  api.get.mockImplementation(source.api.get);
  const principal = { id: "person", subjects: ["user:person"], permissions: [], auth_epoch: 7 };
  const tree = () => <ConfigProvider theme={{ token: { motion: false } }}><MemoryRouter initialEntries={["/onboarding?" + new URLSearchParams(onboardingSelection)]}><OnboardingPage principal={principal} /></MemoryRouter></ConfigProvider>;
  const view = render(tree());
  await screen.findByText("PRIVATE-SPACE");
  api.get.mockImplementation(() => new Promise(() => {}));
  identity.user = { profile: { iss: "issuer", sub: "person", sid: "session-two" } };
  view.rerender(tree());
  expect(screen.queryByText("PRIVATE-SPACE")).not.toBeInTheDocument();
  expect(screen.queryByText("PRIVATE-SESSION")).not.toBeInTheDocument();
  expect(screen.queryByText("PRIVATE-WIKI")).not.toBeInTheDocument();
});

it("does not mark an expired current Wiki as eligible just because its state flag remains valid", async () => {
  const values = onboardingResponses();
  const page = values["/pages/page"] as { revision: { content: Record<string, unknown> } };
  page.revision.content.valid_until = "2000-01-01T00:00:00Z";
  const source = onboardingAPI(values);
  const result = await loadOnboarding(source.api, onboardingSelection, new AbortController().signal);
  expect(onboardingChecks(result).wiki).toBe(false);
});

it("does not treat an older valid Wiki as approval of this task's pending same-commit retirement proposal", async () => {
  const values = onboardingResponses();
  Object.assign(values["/tasks/task"] as object, { status: "review_needed", result: { items: [{ page_id: "page", path: "README.md", status: "review_needed", proposal_id: "pending-retirement" }] } });
  const page = values["/pages/page"] as { revision: Record<string, unknown> };
  Object.assign(page.revision, { publication_kind: "review", proof: { proposal_id: "older-approved-proposal", proposal_kind: "ingest" } });
  const result = await loadOnboarding(onboardingAPI(values).api, onboardingSelection, new AbortController().signal);
  expect(onboardingChecks(result).sync).toBe(false);
});
