import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import type { ChangeHealthCheckResponse } from "@repowise-dev/types/blast-radius";
import { HealthCheckButton, HealthCheckPanel, shortRevspec } from "../../src/blast-radius/health-check-panel.js";

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
  it("runs only on click, and shows nothing until it has", () => {
    const onRun = vi.fn();
    const { container } = render(<HealthCheckPanel state={undefined} />);
    expect(container).toBeEmptyDOMElement();
    render(<HealthCheckButton state={undefined} onRun={onRun} />);
    expect(onRun).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Run health check/ }));
    expect(onRun).toHaveBeenCalledOnce();
  });

  it("shows progress while running", () => {
    render(<HealthCheckButton state={{ status: "running" }} onRun={() => {}} />);
    expect(screen.getByRole("button", { name: /Checking/ })).toBeDisabled();
  });

  it("cuts full shas in the revspec it names", () => {
    expect(shortRevspec(`${"a".repeat(40)}...${"b".repeat(40)}`)).toBe("aaaaaaaa...bbbbbbbb");
    expect(shortRevspec("origin/main...feat/x")).toBe("origin/main...feat/x");
  });

  it("groups findings and names the verdict at the CLI defaults", () => {
    render(<HealthCheckPanel state={{ status: "done", result: RESULT }} />);
    expect(screen.getByText("Fail")).toBeInTheDocument();
    expect(screen.getByText(/Fails on introduced at high/)).toBeInTheDocument();
    expect(screen.getByText(/^Introduced/)).toBeInTheDocument();
    expect(screen.getByText(/^Diff risk/)).toBeInTheDocument();
    expect(screen.getByText(/^Fixed/)).toBeInTheDocument();
    expect(screen.getByText(/added-suppression: noqa added/)).toBeInTheDocument();
  });
});
