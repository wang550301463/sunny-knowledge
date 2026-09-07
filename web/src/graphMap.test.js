import assert from "node:assert/strict";
import { toGraph } from "./graphMap.js";
import test from "node:test";

test("keeps invalidated edges", () => {
  const g = toGraph({
    nodes: [{ id: "pay-api" }, { id: "redis-6" }],
    edges: [{ source: "pay-api", target: "redis-6", invalidAt: "2026-09-01T00:00:00Z", kind: "uses" }],
  });
  assert.equal(g.links[0].invalid, true);
});

test("dims low confidence nodes", () => {
  const g = toGraph({
    nodes: [{ id: "pay-api", confidence: 0.2 }, { id: "policy", confidence: 1 }],
    edges: [],
  });
  assert.equal(g.nodes[0].dim, true);
  assert.equal(g.nodes[1].dim, false);
});
