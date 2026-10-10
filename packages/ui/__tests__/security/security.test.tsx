import { describe, it, expect } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import type { SecurityFinding } from "@repowise-dev/types";
import { SecurityFindingsTable } from "../../src/security/findings-table.js";
import { SeverityDirectoryMatrix } from "../../src/security/severity-directory-matrix.js";
import {
  SecurityPosture,
  countSecurityFindings,
  securityPathClass,
} from "../../src/security/posture.js";

let nextId = 1;
function finding(file_path: string, severity: string, kind = "eval_call"): SecurityFinding {
  return {
    id: nextId++,
    file_path,
    kind,
    severity,
    snippet: "eval(x)",
    detected_at: "2026-10-01T00:00:00Z",
    line_number: 3,
    line_verified: true,
    commit_at: null,
  };
}

const FINDINGS = [
  finding("tests/fixtures/keys.py", "low", "hardcoded_secret"),
  finding("src/app/server.py", "high"),
  finding("docs/setup.md", "med", "hardcoded_secret"),
  finding("src/app/util.py", "med"),
];

describe("securityPathClass", () => {
  it("separates shipped source from tests and docs", () => {
    expect(securityPathClass("src/app/server.py")).toBe("source");
    expect(securityPathClass("packages/ui/__tests__/a.test.tsx")).toBe("test");
    expect(securityPathClass("pkg/foo_test.go")).toBe("test");
    expect(securityPathClass("test_login.py")).toBe("test");
    expect(securityPathClass("docs/guide/index.html")).toBe("docs");
    expect(securityPathClass("CHANGELOG.md")).toBe("docs");
  });
});

describe("countSecurityFindings", () => {
  it("counts by place and severity", () => {
    expect(countSecurityFindings(FINDINGS)).toEqual({
      total: 4,
      source: 2,
      elsewhere: 2,
      sourceHigh: 1,
      high: 1,
      med: 2,
      low: 1,
    });
  });
});

describe("SecurityPosture", () => {
  it("leads with shipped source and says when the scan ran", () => {
    render(<SecurityPosture findings={FINDINGS} scannedAgo="3 days ago" />);
    expect(screen.getByText("In shipped source")).toBeInTheDocument();
    expect(screen.getByText("of 4 findings")).toBeInTheDocument();
    expect(
      screen.getByText(/matched 2 places in shipped source, 1 of them high\. 2 more are in tests and docs/),
    ).toBeInTheDocument();
    expect(screen.getByText("Scanned 3 days ago.")).toBeInTheDocument();
  });

  it("never implies freshness it does not have", () => {
    render(<SecurityPosture findings={FINDINGS} scannedAgo={null} capped />);
    expect(screen.getByText("Scan time not recorded.")).toBeInTheDocument();
    expect(screen.getByText("of 4+ findings")).toBeInTheDocument();
  });

  it("does not headline matches that only sit in tests", () => {
    render(<SecurityPosture findings={[FINDINGS[0]!]} scannedAgo="now" />);
    expect(screen.getByText(/Nothing matched in shipped source\. The one match is in tests and docs/)).toBeInTheDocument();
  });
});

describe("SecurityFindingsTable", () => {
  it("marks severity as a dot and a word, never green or a filled chip", () => {
    const { container } = render(<SecurityFindingsTable findings={FINDINGS} />);
    const table = screen.getByRole("table");
    expect(within(table).getAllByText("Medium")).toHaveLength(2);
    expect(container.innerHTML).not.toContain("--color-success");
    expect(container.querySelector("[class*='bg-[var(--color-accent-primary)]']")).toBeNull();
  });

  it("ranks shipped source first and labels the rest", () => {
    render(<SecurityFindingsTable findings={FINDINGS} />);
    const rows = within(screen.getByRole("table")).getAllByRole("row").slice(1);
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringContaining("src/app/server.py"),
      expect.stringContaining("src/app/util.py"),
      expect.stringContaining("tests/fixtures/keys.py"),
      expect.stringContaining("docs/setup.md"),
    ]);
    expect(rows[2]!.textContent).toContain("test");
  });

  it("wraps the path instead of truncating it", () => {
    render(<SecurityFindingsTable findings={FINDINGS} />);
    const path = screen.getByText("src/app/server.py");
    expect(path.className).not.toContain("break-all");
    expect(path.querySelectorAll("wbr").length).toBeGreaterThan(1);
    expect(path.className).not.toContain("truncate");
  });

  it("keeps every severity option and its unfiltered count when one is picked", () => {
    render(<SecurityFindingsTable findings={FINDINGS} />);
    const group = screen.getByRole("radiogroup", { name: "Severity" });
    fireEvent.click(within(group).getByRole("radio", { name: /High/ }));
    expect(within(group).getByRole("radio", { name: /High/ })).toHaveAttribute("aria-checked", "true");
    expect(within(group).getAllByRole("radio").map((r) => r.textContent)).toEqual([
      "All4",
      "High1",
      "Medium2",
      "Low1",
    ]);
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(2);
  });

  it("offers to clear filters when they hide everything", () => {
    render(<SecurityFindingsTable findings={FINDINGS} />);
    fireEvent.change(screen.getByLabelText("Search findings"), { target: { value: "nothing-here" } });
    expect(screen.getByText("No findings match these filters")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(5);
  });
});

describe("SeverityDirectoryMatrix", () => {
  it("is a plain count table without heat tints", () => {
    const { container } = render(<SeverityDirectoryMatrix findings={FINDINGS} />);
    expect(screen.getByRole("rowheader", { name: "src/app" })).toBeInTheDocument();
    expect(container.querySelector("[style]")).toBeNull();
  });

  it("renders nothing for no findings", () => {
    const { container } = render(<SeverityDirectoryMatrix findings={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
