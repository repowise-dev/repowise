import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, cleanup } from "@testing-library/react";
import { App } from "./App";
import type { WebviewHost } from "../../runtime/rpc";
import type { RepoInit } from "../../../../src/shared/webviewMessages";
import type { DecisionRecordResponse } from "@repowise-dev/api-client/types";

function decision(over: Partial<DecisionRecordResponse>): DecisionRecordResponse {
  return {
    id: "d",
    repository_id: "r1",
    title: "Untitled",
    status: "active",
    context: "",
    decision: "",
    rationale: "",
    alternatives: [],
    consequences: [],
    affected_files: [],
    affected_modules: [],
    tags: [],
    source: "cli",
    evidence_commits: [],
    evidence_file: null,
    evidence_line: null,
    confidence: 1,
    staleness_score: 0,
    superseded_by: null,
    last_code_change: null,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...over,
  };
}

const DECISIONS: DecisionRecordResponse[] = [
  decision({ id: "a", title: "Adopt hexagonal ports", status: "active", context: "We split the app." }),
  decision({ id: "b", title: "Deprecate the legacy client", status: "deprecated" }),
];

const REPO: RepoInit = { id: "r1", name: "repo", headCommit: null, defaultBranch: "main" };

function makeHost(list: DecisionRecordResponse[]): WebviewHost {
  return {
    api: { decisionsList: vi.fn().mockResolvedValue(list) } as unknown as WebviewHost["api"],
    onInit: () => () => {},
    onRefresh: () => () => {},
    onUpdateDone: () => () => {},
    onThemeChanged: () => () => {},
    ready: () => {},
    openFile: () => {},
    copyText: () => {},
    openExternal: () => {},
    openView: () => {},
    updateIndex: () => {},
    setTheme: () => {},
  };
}

describe("decisions App", () => {
  afterEach(() => cleanup());

  it("renders decision items and the detail pane", async () => {
    render(<App host={makeHost(DECISIONS)} repo={REPO} params={{}} refreshToken={0} />);

    // The first record shows in both the list and the auto-selected detail pane.
    expect((await screen.findAllByText("Adopt hexagonal ports")).length).toBeGreaterThan(0);
    expect(screen.getByText("Deprecate the legacy client")).toBeTruthy();
    // First record auto-selects; its context markdown shows in the detail pane.
    expect(screen.getByText("We split the app.")).toBeTruthy();
  });

  it("filters the list by status chip", async () => {
    render(<App host={makeHost(DECISIONS)} repo={REPO} params={{}} refreshToken={0} />);
    await screen.findAllByText("Adopt hexagonal ports");

    fireEvent.click(screen.getByRole("button", { name: /^Deprecated/ }));

    expect(screen.queryByText("Adopt hexagonal ports")).toBeNull();
    expect(screen.getAllByText("Deprecate the legacy client").length).toBeGreaterThan(0);
  });
});
