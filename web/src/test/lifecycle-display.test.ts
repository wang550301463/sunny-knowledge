import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { webcrypto } from "node:crypto";
import { displayReceipt, DisplayStorageUnavailable } from "../lifecycle-display";

beforeEach(() => vi.stubGlobal("crypto", webcrypto));
afterEach(() => vi.unstubAllGlobals());
it("retains one opaque id through remount/retry and stores no page, actor, source or token", async () => {
  const first = await displayReceipt("private-actor", "entry", "private-page", "private-revision");
  const retry = await displayReceipt("private-actor", "entry", "private-page", "private-revision");
  expect(retry.receipt).toEqual(first.receipt);
  first.mark();
  expect((await displayReceipt("private-actor", "entry", "private-page", "private-revision")).receipt.recorded).toBe(true);
  expect(sessionStorage.length).toBe(1);
  const stored = sessionStorage.key(0)! + sessionStorage.getItem(sessionStorage.key(0)!);
  expect(stored).not.toContain("private-");
  expect(stored).not.toContain("access_token");
});
it.each([
  ["other-actor", "entry", "page", "revision"],
  ["actor", "other-entry", "page", "revision"],
  ["actor", "entry", "page", "other-revision"],
])("uses a new event for a different actor, navigation entry or revision", async (actor, entry, page, revision) => {
  const previous = await displayReceipt("actor", "entry", "page", "revision");
  expect((await displayReceipt(actor, entry, page, revision)).receipt.id).not.toBe(previous.receipt.id);
});
it("rejects corrupted persisted keys instead of replacing them with another visit", async () => {
  await displayReceipt("actor", "entry", "page", "revision");
  const key = sessionStorage.key(0)!;
  sessionStorage.setItem(key, '{"id":"bad","recorded":false}');
  await expect(displayReceipt("actor", "entry", "page", "revision")).rejects.toBeInstanceOf(DisplayStorageUnavailable);
  expect(sessionStorage.getItem(key)).toBe('{"id":"bad","recorded":false}');
});