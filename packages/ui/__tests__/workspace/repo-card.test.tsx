import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { ReactFlowProvider } from "@xyflow/react";
import type { NodeProps } from "@xyflow/react";

import { RepoCard } from "../../src/workspace/repo-card.js";
import { WorkspaceGraphNode } from "../../src/workspace/workspace-graph-node.js";

describe("RepoCard", () => {
  it("names the figure as doc confidence, floored", () => {
    render(
      <RepoCard
        repoId="r1"
        alias="api"
        name="api"
        path="./api"
        isPrimary={false}
        stats={{ file_count: 12, doc_coverage_pct: 99.8 }}
        gitSummary={{ hotspot_count: 3 }}
      />,
    );
    expect(screen.getByText("99% doc confidence")).toBeInTheDocument();
    expect(screen.queryByText(/% docs$/)).toBeNull();
  });
});

function renderNode(healthScore: number | null) {
  const data = {
    repoId: "r1",
    name: "api",
    fileCount: 12,
    coveragePct: 99.8,
    healthScore,
    topLanguage: "python",
  };
  return render(
    <ReactFlowProvider>
      <WorkspaceGraphNode {...({ data } as unknown as NodeProps)} />
    </ReactFlowProvider>,
  );
}

describe("WorkspaceGraphNode", () => {
  it("labels mean page confidence as doc confidence, not coverage", () => {
    renderNode(72);
    expect(screen.getByText("99% doc conf.")).toBeInTheDocument();
    expect(screen.queryByText(/% cov$/)).toBeNull();
    expect(screen.getByLabelText("Health score: 72")).toBeInTheDocument();
  });

  it("shows no score for a repo health has not scored", () => {
    renderNode(null);
    expect(screen.getByText("no score")).toBeInTheDocument();
    expect(screen.queryByLabelText(/Health score/)).toBeNull();
  });
});
