import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { GraphCanvas } from "../components/GraphCanvas";
import { decodeGraphResult } from "../graph-api";
import type { GraphParameters } from "../graph-types";
import { graphResult } from "./graph-fixtures";
const params: GraphParameters = { space_ids: ["space"], as_of: null, known_at: null, include_historical: false, relation: { types: ["depends_on"], direction: "incoming", hops: 2 } };
describe("graph direction and keyboard navigation", () => {
  it("keeps the stored arrow direction during incoming traversal and exposes full stable IDs", () => {
    const selected = vi.fn(), data = decodeGraphResult(graphResult());
    const view = render(<GraphCanvas data={data} params={params} onSelect={selected}/>);
    expect(view.container.querySelector("path[marker-end] title")).toHaveTextContent("stable-a → stable-b · depends_on · fact");
    fireEvent.keyDown(screen.getByRole("button", { name: "图中节点 stable-b" }), { key: "Enter" });
    expect(selected).toHaveBeenCalledWith({ kind: "node", id: "stable-b" });
    expect(screen.getByText("关联 Wiki：授权 Wiki B")).toBeInTheDocument();
  });
  it("marks inference visually without changing it into a fact", () => {
    const input = graphResult(); input.graph.edges[0].kind = "inference";
    const view = render(<GraphCanvas data={decodeGraphResult(input)} params={params} onSelect={vi.fn()}/>);
    expect(view.container.querySelector("path[marker-end]")).toHaveAttribute("stroke-dasharray", "5 5");
    expect(view.container.querySelector("path[marker-end] title")).toHaveTextContent("inference");
  });
});