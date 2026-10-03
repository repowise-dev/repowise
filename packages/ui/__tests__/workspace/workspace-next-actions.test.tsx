import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, within } from "@testing-library/react";
import type { NextAction, WorkspaceActionsResponse } from "@repowise-dev/types/actions";

import { WorkspaceNextActions } from "../../src/workspace/workspace-next-actions.js";

function action(id: string, title: string, tier: NextAction["tier"] = "plan"): NextAction {
  return {
    id,
    rule: "fragile_file",
    tier,
    horizons: ["week", "quarter"],
    severity: tier === "act_now" ? "critical" : "high",
    title,
    impact: "Changed 30 times in 90 days.",
    why: [],
    target: { kind: "file", path: "src/a.py", symbol: null },
    surface: "file",
    effort: "M",
    confidence: "high",
    done_when: "Coverage reaches 80%.",
    command: null,
    marker: null,
    evidence_ids: [],
    evidence_total: 0,
    details: [],
    details_total: 0,
    commands: [],
    includes: [],
    fingerprint: "fp",
  };
}

function horizon(actions: NextAction[], total = actions.length) {
  return {
    actions,
    total,
    hidden: 0,
    by_tier: {
      act_now: actions.filter((a) => a.tier === "act_now").length,
      plan: total - actions.filter((a) => a.tier === "act_now").length,
    },
  };
}

const data: WorkspaceActionsResponse = {
  repos: [
    {
      alias: "backend",
      repo_id: "b1",
      status: "available",
      reason: "",
      horizons: { week: horizon([action("x", "Add tests around `poll.py`")], 3), quarter: horizon([]) },
    },
    {
      alias: "frontend",
      repo_id: "f1",
      status: "available",
      reason: "",
      horizons: {
        week: horizon([action("y", "Clean up `app.tsx`", "act_now")]),
        quarter: horizon([]),
      },
    },
    { alias: "docs", repo_id: null, status: "unavailable", reason: "Not indexed yet.", horizons: {} },
  ],
  cross_repo: [
    {
      kind: "breaking_contract",
      title: "Resolve 2 breaking contract changes",
      impact: "A provider changed a route another repository consumes.",
      count: 2,
      repos: ["frontend"],
    },
  ],
};

describe("WorkspaceNextActions", () => {
  it("leads with cross-repo work, then the repository with work to do now", () => {
    render(
      <WorkspaceNextActions
        data={data}
        hrefFor={() => null}
        repoHref={(id) => `/repos/${id}/overview`}
        contractsHref="/workspace/contracts"
      />,
    );
    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(headings).toEqual(["Across repositories", "frontend", "backend"]);
    expect(screen.getByRole("link", { name: "All 3 in backend" })).toHaveAttribute(
      "href",
      "/repos/b1/overview",
    );
    expect(screen.getByText(/Not included: docs \(not indexed yet\)/)).toBeInTheDocument();
    expect(screen.getByText(/5 things worth doing this week/)).toBeInTheDocument();
  });

  it("loads an opened action's prompt from its own repository", async () => {
    const loadPrompt = vi.fn().mockResolvedValue("Prompt from core");
    render(
      <WorkspaceNextActions
        data={data}
        hrefFor={() => null}
        repoHref={(id) => `/repos/${id}/overview`}
        contractsHref="/workspace/contracts"
        loadPrompt={loadPrompt}
      />,
    );
    fireEvent.click(screen.getByRole("listitem", { name: /Open Clean up/ }));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText("Prompt from core")).toBeInTheDocument();
    expect(loadPrompt).toHaveBeenCalledWith("f1", expect.objectContaining({ id: "y" }), expect.any(String));
  });
});
