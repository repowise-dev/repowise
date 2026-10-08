import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import type { WorkspaceCoChangeEntry } from "@repowise-dev/types/workspace";
import { CoChangeTable, coChangeKey } from "../../src/workspace/co-change-table.js";

function cc(
  sourceRepo: string,
  sourceFile: string,
  targetRepo: string,
  targetFile: string,
  strength = 0.5,
  evidence: WorkspaceCoChangeEntry["evidence"] = null,
): WorkspaceCoChangeEntry {
  return {
    source_repo: sourceRepo,
    source_file: sourceFile,
    target_repo: targetRepo,
    target_file: targetFile,
    strength,
    frequency: 3,
    last_date: "2026-06-01",
    evidence,
  };
}

describe("CoChangeTable", () => {
  it("renders each file as repo, dim directory and full filename", () => {
    const rows = [cc("api", "api/a.py", "core", "core/b.py", 0.72), cc("ui", "ui/c.tsx", "core", "core/d.py")];
    render(<CoChangeTable coChanges={rows} />);
    expect(screen.getByText("a.py")).toBeInTheDocument();
    expect(screen.getAllByText("api/").length).toBe(1);
    expect(screen.getByTitle("core/b.py")).toBeInTheDocument();
    expect(screen.getAllByText("core").length).toBe(2);
    expect(screen.getByText("72%")).toBeInTheDocument();
  });

  it("shows sessions and date unless compact", () => {
    const { rerender } = render(<CoChangeTable coChanges={[cc("api", "a.py", "core", "b.py")]} />);
    expect(screen.getByText("Sessions")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    rerender(<CoChangeTable coChanges={[cc("api", "a.py", "core", "b.py")]} compact />);
    expect(screen.queryByText("Sessions")).not.toBeInTheDocument();
    expect(screen.queryByText("3")).not.toBeInTheDocument();
  });

  it("opens a pair on click or Enter and marks the selected row", () => {
    const onSelect = vi.fn();
    const row = cc("api", "a.py", "core", "b.py");
    render(<CoChangeTable coChanges={[row]} onSelect={onSelect} selectedKey={coChangeKey(row)} />);
    const tr = screen.getByText("a.py").closest("tr")!;
    expect(tr).toHaveAttribute("aria-current", "true");
    fireEvent.click(tr);
    fireEvent.keyDown(tr, { key: "Enter" });
    expect(onSelect).toHaveBeenCalledTimes(2);
    expect(onSelect).toHaveBeenCalledWith(row);
  });

  it("keys a pair the same whichever file leads", () => {
    const row = cc("api", "a.py", "core", "b.py");
    const flipped = cc("core", "b.py", "api", "a.py");
    expect(coChangeKey(row)).toBe(coChangeKey(flipped));
  });

  it("shows the empty state when there are no co-changes", () => {
    render(<CoChangeTable coChanges={[]} />);
    expect(screen.getByText(/no cross-repo co-changes/i)).toBeInTheDocument();
  });

  it("collapses the evidence behind a disclosure that names the sample size", () => {
    const row = cc("api", "api/a.py", "core", "core/b.py", 0.8, {
      authors: ["ada@example.com"],
      commit_pairs: [
        { source_sha: "abc12345", target_sha: "def67890", date: "2026-06-01", gap_hours: 3.5 },
      ],
      max_gap_hours: 3.5,
    });
    render(<CoChangeTable coChanges={[row]} />);
    const summary = screen.getByText("1 commit pair");
    expect(summary).toBeInTheDocument();
    // Collapsed: the author and the shas are in the DOM but not yet shown.
    expect(screen.getByText("ada@example.com")).not.toBeVisible();
    fireEvent.click(summary);
    expect(screen.getByText("ada@example.com")).toBeVisible();
    expect(screen.getByText("abc12345")).toBeVisible();
  });

  it("renders a dash for a pair with no evidence recorded", () => {
    render(<CoChangeTable coChanges={[cc("api", "a.py", "core", "b.py")]} />);
    expect(screen.getByText("—")).toBeInTheDocument();
  });
});
