import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { DecisionLineage } from "../../src/decisions/decision-lineage.js";
import type { DecisionLineageEntry } from "@repowise-dev/types/decisions";

const CHAIN: DecisionLineageEntry[] = [
  { id: "d1", title: "Use REST", status: "superseded", source: "inline_marker", relation: "supersedes" },
  { id: "d2", title: "Use gRPC", status: "active", source: "git_archaeology", relation: null },
];

describe("DecisionLineage", () => {
  it("renders nothing for a trivial chain", () => {
    const { container } = render(
      <DecisionLineage lineage={[CHAIN[0]!]} repoId="r1" />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("renders all hops and a relation connector label", () => {
    render(<DecisionLineage lineage={CHAIN} repoId="r1" />);
    expect(screen.getByText("Use REST")).toBeInTheDocument();
    expect(screen.getByText("Use gRPC")).toBeInTheDocument();
    expect(screen.getByText("superseded by")).toBeInTheDocument();
  });

  it("links each hop to its decision detail page", () => {
    render(<DecisionLineage lineage={CHAIN} repoId="r1" />);
    expect(screen.getByText("Use REST").closest("a")).toHaveAttribute(
      "href",
      "/repos/r1/decisions/d1",
    );
  });
});
