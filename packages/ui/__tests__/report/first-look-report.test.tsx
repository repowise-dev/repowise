import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type {
  FirstLookItem,
  FirstLookReport as Report,
  FirstLookStaleness,
} from "@repowise-dev/types/first-look";

import { FirstLookReport } from "../../src/report/first-look";

function item(id: string, section: string, over: Partial<FirstLookItem> = {}): FirstLookItem {
  return {
    id,
    section,
    rule: "rule_" + id,
    title_facts: {},
    locations: [{ path: `src/${id}.py`, line: 7, symbol: null }],
    locations_total: 1,
    confidence: "high",
    confidence_score: null,
    basis: "inferred",
    source_layer: section,
    origin: "production",
    severity: "plan",
    effort: "S",
    priority: 1,
    weight: 0,
    link: { route: "file", params: { path: `src/${id}.py`, line: 7 } },
    prompt: { renderer: "first_look_item", payload_ref: null },
    fingerprint: "f",
    ...over,
  };
}

const secret = item("fl_secret", "security", {
  rule: "hardcoded_password",
  severity: "act_now",
  basis: "measured",
  locations: [{ path: "scripts/admin.py", line: 27, symbol: null }],
  link: { route: "security", params: {} },
  prompt: { renderer: "action", payload_ref: "act_1" },
});
const dead = Array.from({ length: 14 }, (_, n) =>
  item(`fl_dead${n}`, "dead_code", { link: { route: "dead_code", params: {} } }),
);
dead[0] = {
  ...dead[0]!,
  locations: [
    { path: "src/models.py", line: null, symbol: "QueueItem" },
    { path: "src/models.py", line: null, symbol: "QueueStats" },
  ],
  locations_total: 5,
};
const perf = item("fl_perf", "performance", { title_facts: { verify: "pytest tests/test_x.py" } });

function report(over: Partial<Report> = {}): Report {
  return {
    schema_version: 1,
    stamp: {
      commit: "c120332450ea96e2",
      indexed_at: null,
      created_at: "2026-11-20T09:29:49+00:00",
      generator: "template",
      cost: null,
      schema_version: 1,
    },
    repo: { name: "acme-web", branch: "master", files: 18306, health: { score: 7.71, band: "good", band_label: "Good" } },
    top: ["fl_secret", "fl_perf"],
    sections: [
      { key: "security", source_layer: "security", items: [secret], facts: {} },
      { key: "performance", source_layer: "performance", items: [perf], facts: {} },
      { key: "dead_code", source_layer: "dead_code", items: dead, facts: { safe_exports: 59 } },
    ],
    setup: [{ action_id: "act_cov", rule: "coverage_missing", title: "Add a coverage report", command: "repowise coverage add" }],
    unavailable: {},
    prose: {
      writer: "template",
      summary: "Health is 7.7/10 (Good). Security comes first.",
      headline: "16 of the 20 lowest-health files had a bug fix in the last 6 months.",
      titles: { security: "Security", performance: "Performance", dead_code: "Dead code" },
      sections: { security: "1 tracked file holds a hard-coded password.", dead_code: "14 files hold unused exports." },
      items: { fl_secret: "Hard-coded password in 1 tracked file.", fl_perf: "`scan_rows`: list membership check inside a loop." },
      setup: { act_cov: "Record backend coverage." },
    },
    ...over,
  };
}

const hrefFor = (route: string, params: Record<string, string | number>) =>
  route === "file" ? `/r/files/${params.path}${params.line ? `?line=${params.line}` : ""}` : `/r/${route}`;

function renderReport(props: Partial<Parameters<typeof FirstLookReport>[0]> = {}) {
  const loadPrompt = vi.fn().mockResolvedValue("PROMPT TEXT");
  const utils = render(
    <FirstLookReport report={report()} hrefFor={hrefFor} promptFlavor="generic" loadPrompt={loadPrompt} {...props} />,
  );
  return { ...utils, loadPrompt };
}

describe("FirstLookReport", () => {
  it("lays sections out in report order after Do these first", () => {
    const { container } = renderReport();
    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    expect(headings).toEqual(["Do these first", "Security", "Performance", "Dead code", "Turn on more"]);
    expect([...container.querySelectorAll("[data-section]")].map((s) => s.getAttribute("data-section"))).toEqual([
      "security",
      "performance",
      "dead_code",
    ]);
  });

  it("prints the report's prose: summary, intros, item sentences and setup", () => {
    renderReport();
    expect(screen.getByText("Health is 7.7/10 (Good). Security comes first.")).toBeInTheDocument();
    expect(screen.getByText("1 tracked file holds a hard-coded password.")).toBeInTheDocument();
    expect(screen.getAllByText("Hard-coded password in 1 tracked file.")).toHaveLength(2); // Do first + item
    expect(screen.getByText("Record backend coverage.")).toBeInTheDocument();
    expect(screen.getByText(/16 of the 20 lowest-health files/)).toHaveTextContent("measured");
    // Prose marks names with backticks; they set in mono, never as literal ticks.
    const perf = document.getElementById("fl_perf")!;
    expect(within(perf).getByText("scan_rows").tagName).toBe("CODE");
    expect(perf.textContent).not.toContain("`");
  });

  it("lists a Do these first item only where its row is on the page", () => {
    renderReport({ report: report({ top: ["fl_secret", "fl_dead13"] }) });
    const first = within(screen.getByRole("heading", { name: "Do these first" }).parentElement!);
    expect(first.getAllByRole("listitem")).toHaveLength(1);
    expect(first.getByRole("link", { name: /Go to item: Hard-coded password/ })).toHaveAttribute("href", "#fl_secret");
  });

  it("links chips and actions through hrefFor", () => {
    renderReport();
    const chip = screen.getByRole("link", { name: /scripts\/admin\.py\s?:27/ });
    expect(chip).toHaveAttribute("href", "/r/files/scripts/admin.py?line=27");
    const secretRow = document.getElementById("fl_secret")!;
    expect(within(secretRow).getByRole("link", { name: "Open in app" })).toHaveAttribute("href", "/r/security");
    expect(screen.getByRole("link", { name: "Open Security in the app" })).toHaveAttribute("href", "/r/security");
  });

  it("shows a secret's location, never a value", () => {
    renderReport();
    const row = document.getElementById("fl_secret")!;
    expect(within(row).getByText("value redacted")).toBeInTheDocument();
    expect(row.textContent).not.toMatch(/password\s*=|["']/);
  });

  it("marks act_now and shows basis, confidence and effort", () => {
    renderReport();
    const row = within(document.getElementById("fl_secret")!);
    expect(row.getByText("do now")).toBeInTheDocument();
    expect(row.getByText("measured")).toBeInTheDocument();
    expect(row.getByText("high confidence")).toBeInTheDocument();
    expect(within(document.getElementById("fl_perf")!).queryByText("do now")).toBeNull();
    expect(screen.getByText("verify: pytest tests/test_x.py")).toBeInTheDocument();
  });

  it("names each symbol when one file holds several, and counts locations past the cap", () => {
    renderReport();
    const row = within(document.getElementById("fl_dead0")!);
    expect(row.getAllByRole("link", { name: /^src\/models\.py/ })).toHaveLength(2);
    expect(row.getByText("QueueItem")).toBeInTheDocument();
    expect(row.getByText("QueueStats")).toBeInTheDocument();
    expect(row.getByText("+ 3 more")).toBeInTheDocument();
  });

  it("caps a section and counts the rest in the app", () => {
    renderReport();
    expect(document.querySelectorAll('[data-section="dead_code"] article')).toHaveLength(12);
    expect(screen.getByRole("link", { name: "2 more in the app" })).toHaveAttribute("href", "/r/dead_code");
  });

  it("strikes fixed items and marks changed ones when the index moved", () => {
    const staleness: FirstLookStaleness = {
      report_commit: "c120332450ea96e2",
      current_commit: "9f2e1aa000",
      behind: true,
      fixed: ["fl_perf"],
      changed: ["fl_dead0"],
      new: [],
      unknown: {},
    };
    renderReport({ staleness });
    expect(document.getElementById("fl_perf")).toHaveAttribute("data-state", "fixed");
    expect(screen.getByText("Fixed since c120332")).toBeInTheDocument();
    expect(screen.getByText("Changed since c120332")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("The index is now at 9f2e1aa: 1 fixed, 1 changed.");
  });

  it("says nothing about staleness while the index is at the report's commit", () => {
    renderReport({
      staleness: { report_commit: "c1", current_commit: "c1", behind: false, fixed: [], changed: [], new: [], unknown: {} },
    });
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("offers Write it with AI only when the host can rewrite", () => {
    const onRewrite = vi.fn();
    const { rerender, loadPrompt } = renderReport();
    expect(screen.queryByRole("button", { name: /Write it with AI/ })).toBeNull();
    rerender(
      <FirstLookReport
        report={report()}
        hrefFor={hrefFor}
        promptFlavor="generic"
        loadPrompt={loadPrompt}
        canRewrite
        onRewrite={onRewrite}
        rewriteCost={0.003}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Write it with AI, about <$0.01" }));
    expect(onRewrite).toHaveBeenCalledOnce();
  });

  it("names the model and its cost on a model-written report", () => {
    renderReport({
      report: report({ stamp: { ...report().stamp, generator: "claude-haiku-5-5", cost: 0.25 } }),
    });
    expect(screen.getByText("Written by claude-haiku-5-5, $0.25")).toBeInTheDocument();
  });

  it("asks the host for the item's prompt", async () => {
    const { loadPrompt } = renderReport();
    fireEvent.click(screen.getByRole("button", { name: "Fix with your agent: Hard-coded password in 1 tracked file." }));
    await waitFor(() => expect(loadPrompt).toHaveBeenCalledWith("fl_secret", expect.any(String)));
    expect(await screen.findByText("PROMPT TEXT")).toBeInTheDocument();
  });
});
