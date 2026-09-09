import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  displayNavigationEntry,
  DisplayStorageUnavailable,
} from "../lifecycle-display";

const { webcrypto } = await vi.importActual<{ webcrypto: Crypto }>("node:crypto");
beforeEach(() => {
  vi.stubGlobal("crypto", webcrypto);
  window.history.replaceState({ idx: 0, usr: { retained: true } }, "");
});
afterEach(() => vi.unstubAllGlobals());

it("retains an opaque identifier on the same browser entry across refresh/remount", () => {
  const before = window.history.state;
  const first = displayNavigationEntry("default");
  expect(displayNavigationEntry("default")).toBe(first);
  expect(first).toMatch(/^history:[a-f0-9-]{36}$/);
  expect(window.history.state).toMatchObject(before);
  expect(Object.keys(window.history.state)).toEqual([
    "idx", "usr", "knowledgeLifecycleEntry",
  ]);
});

it("distinguishes new full-document navigation even when React Router reuses default", () => {
  const previous = displayNavigationEntry("default");
  const priorState = window.history.state;
  window.history.replaceState({ idx: 0 }, "");
  expect(displayNavigationEntry("default")).not.toBe(previous);
  window.history.replaceState(priorState, "");
  expect(displayNavigationEntry("default")).toBe(previous);
});

it("keeps React Router unique navigation keys without rewriting its history state", () => {
  const before = window.history.state;
  expect(displayNavigationEntry("unique-router-key")).toBe("router:unique-router-key");
  expect(window.history.state).toEqual(before);
});

it("does not mint a replacement for a corrupt saved history identifier", () => {
  window.history.replaceState({ idx: 0, knowledgeLifecycleEntry: "corrupt" }, "");
  expect(() => displayNavigationEntry("default")).toThrow(DisplayStorageUnavailable);
  expect(window.history.state.knowledgeLifecycleEntry).toBe("corrupt");
});

it("stops recording when history identity cannot be persisted", () => {
  const replacement = vi.spyOn(window.history, "replaceState").mockImplementation(() => {});
  try {
    expect(() => displayNavigationEntry("default")).toThrow(DisplayStorageUnavailable);
  } finally {
    replacement.mockRestore();
  }
});
