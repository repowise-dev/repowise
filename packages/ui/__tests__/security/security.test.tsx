import { describe, it, expect } from "vitest";
import { render, screen, fireEvent, within } from "@testing-library/react";
import type { SecurityFinding } from "@repowise-dev/types";
import { SecurityFindingsTable } from "../../src/security/findings-table.js";
import { SeverityDirectoryMatrix } from "../../src/security/severity-directory-matrix.js";
import { FindingsByDirectory } from "@repowise-dev/ui/security/findings-by-directory";
import { FindingsByDirectory as FromBarrel } from "@repowise-dev/ui/security";
import {
  SecurityPosture,
  countSecurityFindings,
  postureCountsFromSummary,
} from "../../src/security/posture.js";

let nextId = 1;
function finding(file_path: string, severity: string, kind = "eval_call"): SecurityFinding {
  const top = file_path.split("/")[0];
  return {
    id: nextId++,
    file_path,
    // The server's class; these fixtures sit where the name says.
    path_class: top === "tests" ? "test" : top === "docs" ? "docs" : "source",
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

describe("postureCountsFromSummary", () => {
  it("reads the server's split over every finding", () => {
    expect(
      postureCountsFromSummary({
        scanned_at: null,
        total: 10,
        class_counts: { source: 1, test: 6, docs: 3 },
        source_high: 1,
      }),
    ).toEqual({ total: 10, source: 1, elsewhere: 9, sourceHigh: 1 });
  });

  it("is null for a server that sends no split", () => {
    expect(postureCountsFromSummary({ scanned_at: null })).toBeNull();
    expect(postureCountsFromSummary(null)).toBeNull();
  });
});

describe("countSecurityFindings", () => {
  it("counts by place and severity", () => {
    expect(countSecurityFindings(FINDINGS)).toEqual({
      total: 4,
      source: 2,
      elsewhere: 2,
      sourceHigh: 1,
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

  it("prefers the server's counts, which are never capped", () => {
    render(
      <SecurityPosture
        counts={{ total: 10, source: 1, elsewhere: 9, sourceHigh: 1 }}
        findings={FINDINGS}
        scannedAgo="now"
        capped
      />,
    );
    expect(screen.getByText("of 10 findings")).toBeInTheDocument();
    expect(screen.getByText(/1 place in shipped source, 1 of them high\. 9 more are in tests and docs/)).toBeInTheDocument();
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

  it("leads with the file name and keeps the whole path as its name", () => {
    render(<SecurityFindingsTable findings={FINDINGS} />);
    const name = screen.getByText("server.py");
    expect(name.closest("[aria-hidden]")?.textContent?.startsWith("server.py")).toBe(true);
    expect(screen.getByText(/^src\/app\/server\.py(:\d+)?$/).className).toContain("sr-only");
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

describe("FindingsByDirectory (deprecated)", () => {
  it("still resolves at its old subpath and renders the directory table", () => {
    expect(FindingsByDirectory).toBe(SeverityDirectoryMatrix);
    expect(FromBarrel).toBe(SeverityDirectoryMatrix);
    render(<FindingsByDirectory findings={FINDINGS} depth={2} />);
    expect(screen.getByRole("rowheader", { name: "src/app" })).toBeInTheDocument();
  });
});
