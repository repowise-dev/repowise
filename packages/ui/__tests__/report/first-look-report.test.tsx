import { afterEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type {
  FirstLookLivingItem,
  FirstLookLivingReport,
} from "@repowise-dev/types/first-look";

import { FirstLookReport, RepoReportCard, contourPaths } from "../../src/report/first-look";

function item(id: string, section: string, over: Partial<FirstLookLivingItem> = {}): FirstLookLivingItem {
  return {
    id,
    section,
    rule: "rule_" + id,
    rules: ["rule_" + id],
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
    members: [],
    status: "open",
    status_reason: null,
    status_until: null,
    change: null,
    ...over,
  };
}

const secrets = item("fl_secret", "security", {
  rule: "hardcoded_password",
  rules: ["hardcoded_password", "hardcoded_secret"],
  severity: "act_now",
  locations: [
    { path: "scripts/seed.py", line: 20, symbol: null },
    { path: "scripts/admin.py", line: 27, symbol: null },
  ],
  locations_total: 2,
  link: { route: "security", params: {} },
});
const perf = item("fl_perf", "performance", { status: "acknowledged", status_reason: "batched later" });
const fns = Array.from({ length: 8 }, (_, n) =>
  item(`fl_fn${n}`, "health", {
    rule: "finding",
    locations: [{ path: `web/Board${n}.tsx`, line: 10 + n, symbol: `Board${n}` }],
    title_facts: { ccn: 200 + n, nloc: 600, importers: 7, changes_90d: 46 },
  }),
);
const cycle = item("fl_cycle", "hygiene", { rule: "import_cycle", severity: "improve", change: "new" });
const unreachable = item("fl_dead", "dead_code", { rule: "unreachable_file", title_facts: { lines: 429 } });
const exports_ = item("fl_exp", "dead_code", { rule: "safe_to_delete", title_facts: { symbols: 10, lines: 93 } });
const doc = item("fl_doc", "docs", { rule: "broken_doc_refs", title_facts: { references: 3 } });

function report(over: Partial<FirstLookLivingReport> = {}): FirstLookLivingReport {
  return {
    schema_version: 1,
    stamp: {
      commit: "c120332450ea96e2",
      indexed_at: null,
      created_at: "2026-11-21T09:00:00+00:00",
      generator: "template",
      cost: null,
      schema_version: 1,
    },
    prose_source: { writer: "template", written_at: null, cost_usd: null },
    repo: {
      name: "acme-web",
      branch: "main",
      files: 1200,
      health: { score: 7.7, band: "good", band_label: "Good" },
      headline_facts: [{ id: "defect_accuracy", lift: 8.28 }],
    },
    top: ["fl_secret", "fl_perf", "fl_fn0"],
    sections: [
      { key: "security", source_layer: "security", items: [secrets], facts: {} },
      { key: "performance", source_layer: "performance", items: [perf], facts: { production_causes: 40 } },
      { key: "health", source_layer: "code_health", items: fns, facts: {} },
      { key: "tests", source_layer: "test_intelligence", items: [], facts: { line_pct: 52.1, branch_pct: 42.7 } },
      { key: "dead_code", source_layer: "dead_code", items: [unreachable, exports_], facts: { safe_exports: 10, unreachable_files: 1 } },
      { key: "docs", source_layer: "doc_drift", items: [doc], facts: {} },
      { key: "hygiene", source_layer: "graph", items: [cycle], facts: {} },
    ],
    setup: [],
    unavailable: {},
    prose: {
      writer: "template",
      summary: "Health is 7.7/10. Security comes first.",
      headline: "16 of the 20 lowest-health files had a bug fix in the last 6 months.",
      titles: { security: "Security", performance: "Performance", health: "Code health", tests: "Tests", dead_code: "Dead code", docs: "Docs", hygiene: "Repo hygiene" },
      sections: { security: "Deleting the line is not enough.", tests: "Measured across 900 files." },
      items: {
        fl_secret: "Rotate 2 hard-coded credentials.",
        fl_perf: "`scan_rows` checks list membership inside a loop.",
        fl_cycle: "A new import cycle between 2 files.",
      },
    },
    baseline: {
      stamp: {
        commit: "a41b9c0e2d7f",
        indexed_at: null,
        created_at: "2026-11-20T09:00:00+00:00",
        generator: "template",
        cost: null,
        schema_version: 1,
      },
      resolved: [
        {
          ...item("fl_vendored", "hygiene", { rule: "vendored_in_tree", status: "resolved" }),
          text: "A vendored file sits in the code tree.",
        },
      ],
    },
    ...over,
  };
}

const hrefFor = (route: string, params: Record<string, string | number>) =>
  route === "file" ? `/r/files/${params.path}${params.line ? `?line=${params.line}` : ""}` : `/r/${route}`;

function renderReport(props: Partial<Parameters<typeof FirstLookReport>[0]> = {}) {
  const loadPrompt = vi.fn().mockResolvedValue("PROMPT TEXT");
  const utils = render(
    <FirstLookReport report={report()} hrefFor={hrefFor} overviewHref="/r/overview" loadPrompt={loadPrompt} {...props} />,
  );
  return { ...utils, loadPrompt };
}

afterEach(() => vi.restoreAllMocks());

describe("FirstLookReport", () => {
  it("reads as one document: strip, Start here, then sections in report order", () => {
    const { container } = renderReport();
    const strip = screen.getByRole("navigation", { name: "Repo report" });
    expect(within(strip).getAllByRole("link").map((a) => a.getAttribute("href"))).toEqual([
      "#security",
      "#performance",
      "#health",
      "#tests",
      "#dead_code",
      "#docs",
      "#hygiene",
    ]);
    expect(within(strip).getByRole("link", { name: /Dead code/ })).toHaveTextContent("11");
    const order = [...container.querySelectorAll("[data-section]")].map((s) => s.getAttribute("data-section"));
    expect(order).toEqual(["security", "performance", "health", "dead_code", "docs", "tests", "hygiene"]);
    expect(screen.getByRole("heading", { name: "Start here" })).toBeInTheDocument();
  });

  it("prints only the report's prose, with backtick names as code", () => {
    renderReport();
    expect(screen.getByText("Health is 7.7/10. Security comes first.")).toBeInTheDocument();
    expect(screen.getByText(/16 of the 20 lowest-health files/)).toHaveTextContent("measured");
    expect(screen.getByText("8.3×")).toBeInTheDocument();
    const perfRow = document.getElementById("fl_perf")!;
    expect(within(perfRow).getByText("scan_rows").tagName).toBe("CODE");
    expect(perfRow.textContent).not.toContain("`");
  });

  it("shows a grouped item once with every location, values never", () => {
    renderReport();
    const row = within(document.getElementById("fl_secret")!);
    expect(row.getByText("Rotate 2 hard-coded credentials.")).toBeInTheDocument();
    expect(row.getByRole("link", { name: /scripts\/seed\.py\s?:\s?20/ })).toHaveAttribute("href", "/r/files/scripts/seed.py?line=20");
    expect(row.getByRole("link", { name: /scripts\/admin\.py\s?:\s?27/ })).toBeInTheDocument();
    expect(row.getByText(/values redacted/)).toBeInTheDocument();
  });

  it("measures progress against the first look and marks each item's status", () => {
    renderReport();
    expect(screen.getByText(/resolved · /).textContent).toMatch(/1 resolved · 1 acknowledged · 1 new since the first look/);
    expect(within(document.getElementById("fl_perf")!).getByText("acknowledged")).toHaveAttribute("data-status", "acknowledged");
    expect(within(document.getElementById("fl_perf")!).getByText(/"batched later"/)).toBeInTheDocument();
    expect(within(document.getElementById("fl_cycle")!).getByText("new")).toBeInTheDocument();
    const gone = document.getElementById("fl_vendored")!;
    expect(gone).toHaveAttribute("data-status", "resolved");
    expect(within(gone).getByText("A vendored file sits in the code tree.")).toBeInTheDocument();
  });

  it("lays code health, dead code and docs out as compact tables, capped", () => {
    renderReport();
    const health = document.querySelector('[data-section="health"]')!;
    expect(within(health as HTMLElement).getAllByRole("row")).toHaveLength(7); // head + 6
    expect(within(health as HTMLElement).getByRole("columnheader", { name: "Max CCN" })).toBeInTheDocument();
    expect(within(health as HTMLElement).getByText("200")).toBeInTheDocument();
    expect(within(health as HTMLElement).getByRole("link", { name: "2 more in the app" })).toHaveAttribute("href", "/r/health_file");
    const dead = document.querySelector('[data-section="dead_code"]') as HTMLElement;
    expect(within(dead).getByText("429")).toBeInTheDocument();
    expect(within(dead).getByText("10")).toBeInTheDocument();
    const docs = document.querySelector('[data-section="docs"]') as HTMLElement;
    expect(within(docs).getByRole("columnheader", { name: "Broken" })).toBeInTheDocument();
  });

  it("copies the section's prompt from its header and an item's from its row", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    const { loadPrompt } = renderReport();
    const security = document.querySelector('[data-section="security"]') as HTMLElement;
    await act(async () => fireEvent.click(within(security).getAllByRole("button", { name: /Fix with agent/ })[0]!));
    expect(loadPrompt).toHaveBeenCalledWith({ section: "security" }, "generic");
    await waitFor(() => expect(writeText).toHaveBeenCalledWith("PROMPT TEXT"));
    await act(async () =>
      fireEvent.click(within(document.getElementById("fl_secret")!).getByRole("button", { name: /^Copy prompt for this item:/ })),
    );
    expect(loadPrompt).toHaveBeenCalledWith({ item: "fl_secret" }, "generic");
    await act(async () => fireEvent.click(screen.getByRole("button", { name: "Hand to agent" })));
    expect(loadPrompt).toHaveBeenCalledWith(null, "generic");
  });

  it("marks an item, asking for a reason where one is due", async () => {
    const onMark = vi.fn().mockResolvedValue(undefined);
    renderReport({ onMark });
    const row = within(document.getElementById("fl_secret")!);
    fireEvent.click(row.getByRole("button", { name: /^Mark:/ }));
    fireEvent.click(await screen.findByRole("button", { name: /^Done/ }));
    expect(onMark).toHaveBeenCalledWith(expect.objectContaining({ id: "fl_secret" }), "done", null);

    fireEvent.click(row.getByRole("button", { name: /^Mark:/ }));
    fireEvent.click(await screen.findByRole("button", { name: /^Not an issue/ }));
    fireEvent.change(screen.getByLabelText("Reason"), { target: { value: "test fixture" } });
    fireEvent.click(screen.getByRole("button", { name: "Save" }));
    expect(onMark).toHaveBeenLastCalledWith(expect.objectContaining({ id: "fl_secret" }), "false_positive", "test fixture");
  });

  it("drops every control in the export view", async () => {
    vi.spyOn(window, "scrollTo").mockImplementation(() => {});
    renderReport({ onMark: vi.fn().mockResolvedValue(undefined) });
    // Every control carries the class the export view and print hide.
    const controls = screen.getAllByRole("button", { name: /Fix with agent|Hand to agent|Copy prompt|^Mark|Share/ });
    expect(controls.length).toBeGreaterThan(5);
    for (const c of controls) expect(c.closest(".first-look-control")).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Share/ }));
    fireEvent.click(await screen.findByRole("button", { name: /^Preview export/ }));
    expect(document.querySelector("[data-export]")).not.toBeNull();
    const rules = [...document.querySelectorAll("style")].map((s) => s.textContent).join("");
    expect(rules).toContain("[data-export] .first-look-control{display:none!important}");
    // The page's own rule hides them: nothing to press, nothing announced.
    expect(screen.queryAllByRole("button", { name: /Fix with agent|Hand to agent|Copy prompt|^Mark/ })).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Back to the app view" }));
    expect(document.querySelector("[data-export]")).toBeNull();
  });

  it("says who wrote the wording, and offers a rewrite only when the host can, with its price", () => {
    const onRewrite = vi.fn();
    const { rerender, loadPrompt } = renderReport();
    expect(screen.queryByRole("button", { name: /Rewrite/ })).toBeNull();
    rerender(
      <FirstLookReport
        report={report()}
        hrefFor={hrefFor}
        overviewHref="/r/overview"
        loadPrompt={loadPrompt}
        canRewrite
        onRewrite={onRewrite}
        rewriteCost={0.25}
      />,
    );
    expect(screen.getByText("Template wording")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Rewrite with your model (~$0.25)" }));
    expect(onRewrite).toHaveBeenCalledOnce();
  });

  it("draws the header contours from the commit, the same every time", () => {
    expect(contourPaths("c1203324")).toEqual(contourPaths("c1203324"));
    expect(contourPaths("c1203324")).not.toEqual(contourPaths("9f2e1aa0"));
  });
});

describe("RepoReportCard", () => {
  it("shows progress since the first look, the top three and a way in", () => {
    render(<RepoReportCard report={report()} href="/r/first-look" />);
    expect(screen.getByRole("heading", { name: "Repo report" })).toBeInTheDocument();
    expect(screen.getByText(/new since Nov 20, 2026/).textContent).toMatch(/1 resolved · 1 acknowledged · 1 new since/);
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
    expect(screen.getByRole("link", { name: /Open report/ })).toHaveAttribute("href", "/r/first-look");
  });
});

describe("FirstLookReport without a stored first look", () => {
  it("shows the report now with no progress line", () => {
    render(
      <FirstLookReport
        report={report({ baseline: { stamp: null, resolved: [] } })}
        hrefFor={hrefFor}
        overviewHref="/r/overview"
        loadPrompt={vi.fn()}
      />,
    );
    expect(screen.queryByText(/since the first look/)).toBeNull();
    expect(screen.getByRole("heading", { level: 1, name: "acme-web" })).toBeInTheDocument();
  });
});

describe("Code health without recorded numbers", () => {
  it("drops an empty column, and falls back to the prose rows when no column has a number", () => {
    const partial = report();
    const health = partial.sections.find((s) => s.key === "health")!;
    health.items = health.items.map((i) => ({ ...i, title_facts: { ccn: 200 } }));
    const { unmount } = render(
      <FirstLookReport report={partial} hrefFor={hrefFor} overviewHref="/r/overview" loadPrompt={vi.fn()} />,
    );
    let section = document.querySelector('[data-section="health"]') as HTMLElement;
    expect(within(section).getAllByRole("columnheader").map((c) => c.textContent)).toEqual(["File", "Max CCN"]);
    unmount();

    const bare = report();
    const h2 = bare.sections.find((s) => s.key === "health")!;
    h2.items = h2.items.map((i) => ({ ...i, title_facts: {} }));
    bare.prose.items = { ...bare.prose.items, fl_fn0: "Split Board0 into 7 helpers. CCN 248, 605 lines." };
    render(<FirstLookReport report={bare} hrefFor={hrefFor} overviewHref="/r/overview" loadPrompt={vi.fn()} />);
    section = document.querySelector('[data-section="health"]') as HTMLElement;
    expect(within(section).queryByRole("table")).toBeNull();
    expect(within(section).getByText("Split Board0 into 7 helpers. CCN 248, 605 lines.")).toBeInTheDocument();
    expect(within(section).queryByText("—")).toBeNull();
  });
});

describe("Prose source and mark reasons", () => {
  it("dates a model's wording and shows a marked item's reason on its pill", () => {
    const r = report({
      prose_source: { writer: "claude-haiku-5-5", written_at: "2026-11-20T09:00:00+00:00", cost_usd: 0.003 },
    });
    render(
      <FirstLookReport report={r} hrefFor={hrefFor} overviewHref="/r/overview" loadPrompt={vi.fn()} canRewrite onRewrite={vi.fn()} />,
    );
    const wording = screen.getByText(/^Wording by your model, Nov 20, 2026/);
    expect(wording).toHaveAttribute("title", "claude-haiku-5-5");
    expect(wording).toHaveTextContent("(claude-haiku-5-5)");
    expect(screen.getByRole("button", { name: "Rewrite" })).toBeInTheDocument();
    const pill = within(document.getElementById("fl_perf")!).getByText(/^acknowledged/);
    expect(pill).toHaveAttribute("title", "batched later");
    expect(pill).toHaveTextContent("acknowledged: batched later");
  });
});
