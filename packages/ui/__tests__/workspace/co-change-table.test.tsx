import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import type { WorkspaceCoChangeEntry } from "@repowise-dev/types/workspace";
import { CoChangeTable } from "../../src/workspace/co-change-table.js";

function cc(
  sourceRepo: string,
  sourceFile: string,
  targetRepo: string,
  targetFile: string,
  strength = 5,
): WorkspaceCoChangeEntry {
  return {
    source_repo: sourceRepo,
    source_file: sourceFile,
    target_repo: targetRepo,
    target_file: targetFile,
    strength,
    frequency: 3,
    last_date: "2026-06-01",
  };
}

describe("CoChangeTable (virtualized)", () => {
  it("renders a row per co-change with repos and files", () => {
    const rows = [
      cc("api", "api/a.py", "core", "core/b.py", 8),
      cc("ui", "ui/c.tsx", "core", "core/d.py", 2),
    ];
    render(<CoChangeTable coChanges={rows} />);
    expect(screen.getByText("api/a.py")).toBeInTheDocument();
    expect(screen.getByText("core/b.py")).toBeInTheDocument();
    expect(screen.getByText("ui/c.tsx")).toBeInTheDocument();
    expect(screen.getByText("core/d.py")).toBeInTheDocument();
    expect(screen.getAllByText("core").length).toBeGreaterThan(0);
  });

  it("shows the frequency cell when not compact", () => {
    render(<CoChangeTable coChanges={[cc("api", "a.py", "core", "b.py")]} />);
    expect(screen.getByText("3x")).toBeInTheDocument();
  });

  it("hides the frequency cell when compact", () => {
    render(<CoChangeTable coChanges={[cc("api", "a.py", "core", "b.py")]} compact />);
    expect(screen.queryByText("3x")).not.toBeInTheDocument();
  });

  it("shows the empty state when there are no co-changes", () => {
    render(<CoChangeTable coChanges={[]} />);
    expect(screen.getByText(/no cross-repo co-changes/i)).toBeInTheDocument();
  });
});
