import { describe, expect, it } from "vitest";
import { decodeGraphResult, matchesGraphSource, graphSelectionEvidence } from "../graph-api";
import { graphResult, graphSnapshot } from "./graph-fixtures";

describe("graph public evidence boundary", () => {
  it("accepts only bounded evidence-backed structure and preserves stable IDs", () => {
    const result = decodeGraphResult(graphResult());
    expect(result.graph.edges[0].source).toBe("stable-a");
    expect(graphSelectionEvidence(result, { kind: "edge", id: "edge-ab" })).toHaveLength(1);
  });
  it.each(["missing-fragment", "missing-endpoint", "unknown-relation", "oversized", "missing-citation", "null-payload"])("rejects malformed %s without rendering a partial graph", (fault) => {
    const result = graphResult();
    if (fault === "missing-fragment") result.graph.edges[0].fragment_ids = ["not-authorized"];
    if (fault === "missing-endpoint") result.graph.edges[0].target = "not-returned";
    if (fault === "unknown-relation") result.graph.edges[0].type = "guessed";
    if (fault === "oversized") result.graph.nodes = Array.from({ length: 101 }, (_, i) => ({ id: String(i), fragment_ids: ["fragment-a"] }));
    if (fault === "missing-citation") result.evidence = [];
    expect(() => decodeGraphResult(fault === "null-payload" ? null : result)).toThrow("invalid_graph_response");
  });
  it("validates the original source identity and digest without confusing Wiki and source spaces", () => {
    const evidence = decodeGraphResult(graphResult()).evidence[0];
    expect(matchesGraphSource(graphSnapshot, evidence)).toBe(true);
    for (const field of ["id", "resource_id", "source_id", "source_revision", "path", "kind", "sha256", "space_id"])
      expect(matchesGraphSource({ ...graphSnapshot, [field]: "wrong" }, evidence)).toBe(false);
    expect(matchesGraphSource({ ...graphSnapshot, text: "" }, evidence)).toBe(false);
  });
});