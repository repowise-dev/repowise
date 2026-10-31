import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ChangeHealthCheckResponse } from "@repowise-dev/types/blast-radius";
import { HealthCheckPanel } from "../../src/blast-radius/health-check-panel.js";

const RESULT: ChangeHealthCheckResponse = {
  change_id: "branch:feat/x",
  revspec: "origin/main...feat/x",
  status: "complete",
  explanation: null,
  scope: {},
  skipped: {},
  gate: { passed: false, verdict: "fail", fail_on: "introduced", fail_at: "high", failing_count: 1, warning_count: 1, note_count: 1, notes: [] },
  findings: [
    { source: "health", rule: "deep_nesting", level: "error", file_path: "src/a.py", line_number: 3, symbol: null, message: "Deep nesting (introduced, high)", fingerprint: "1", change_kind: "introduced", severity: "high" },
    { source: "diff-risk", rule: "added-suppression", level: "warning", file_path: "src/b.py", line_number: 9, symbol: null, message: "noqa added", fingerprint: "2" },
    { source: "health", rule: "long_function", level: "note", file_path: "src/c.py", line_number: 1, symbol: null, message: "Long function (resolved, medium)", fingerprint: "3", change_kind: "resolved", severity: "medium" },
  ],
};

describe("HealthCheckPanel", () => {
  it("runs only on click", () => {
    const onRun = vi.fn();
    render(<HealthCheckPanel state={undefined} onRun={onRun} />);
    expect(onRun).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Run health check/ }));
    expect(onRun).toHaveBeenCalledOnce();
  });

  it("shows progress while running", () => {
    render(<HealthCheckPanel state={{ status: "running" }} onRun={() => {}} />);
    expect(screen.getByRole("button", { name: /Checking/ })).toBeDisabled();
  });

  it("groups findings and names the verdict at the CLI defaults", () => {
    render(<HealthCheckPanel state={{ status: "done", result: RESULT }} onRun={() => {}} />);
    expect(screen.getByText("Fail")).toBeInTheDocument();
    expect(screen.getByText(/fails on introduced at/)).toBeInTheDocument();
    expect(screen.getByText(/^Introduced/)).toBeInTheDocument();
    expect(screen.getByText(/^Diff risk/)).toBeInTheDocument();
    expect(screen.getByText(/^Fixed/)).toBeInTheDocument();
    expect(screen.getByText(/added-suppression: noqa added/)).toBeInTheDocument();
  });
});
