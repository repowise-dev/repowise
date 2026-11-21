import { describe, it, expect } from "vitest";
import { render, screen, within } from "@testing-library/react";

import {
  CHECK_PROMPTS,
  DEFAULT_SAVINGS_COPY,
  SavingsChecks,
  SavingsPageBody,
  checksHaveOutcomes,
  switchProfilePrompt,
  toolAvailability,
  type CheckAgentView,
  type SavingsCheckView,
  type SavingsView,
} from "../../src/savings";

const copy = DEFAULT_SAVINGS_COPY.checks;

const NULLS = {
  high: null,
  medium: null,
  low: null,
  decisions_accepted: null,
  decisions_candidate: null,
  decisions_none: null,
  findings: null,
  files: null,
  items: null,
  high_confidence: null,
};

function row(tool: string, calls: number, fields: Partial<SavingsCheckView> = {}): SavingsCheckView {
  return { tool, calls, outcome_calls: 0, ...NULLS, ...fields };
}

const RECORDED: SavingsCheckView[] = [
  row("get_change_risk", 1311, { outcome_calls: 1311, high: 214, medium: 406, low: 691 }),
  row("get_why", 465, { outcome_calls: 465, decisions_accepted: 188, decisions_candidate: 0, decisions_none: 277 }),
  row("get_risk", 490, { outcome_calls: 490, high: 96, medium: 172, low: 222 }),
  row("get_health", 843, { outcome_calls: 843, findings: 862, files: 311 }),
  row("get_dead_code", 0),
];

const CORE = ["get_answer", "get_context", "get_symbol", "search_codebase", "get_why"];
const STANDARD = [...CORE, "get_overview", "get_change_risk", "get_risk", "get_health", "get_dead_code"];
const PROFILES = { core: CORE, standard: STANDARD };

const claude: CheckAgentView = {
  id: "claude-code",
  name: "Claude Code",
  profile: "standard",
  tools: STANDARD,
  lastCallAt: new Date(Date.now() - 4 * 60_000).toISOString(),
};
const cursor: CheckAgentView = {
  id: "cursor",
  name: "Cursor",
  profile: "core",
  tools: CORE,
  lastCallAt: null,
};

function item(name: string): HTMLElement {
  return screen.getByText(name).closest("li") as HTMLElement;
}

describe("SavingsChecks", () => {
  it("leads with three summary numbers and rows that say what Repowise returned", () => {
    render(<SavingsChecks rows={RECORDED} agents={[claude, cursor]} profiles={PROFILES} copy={copy} agentsHref="/agents" />);
    expect(screen.getByText("Risky changes caught")).toBeTruthy();
    expect(screen.getByText("Changes Repowise rated high risk for this repository, out of 1,311 it ranked.")).toBeTruthy();
    // Problems pointed out: health findings plus files flagged high risk.
    expect(screen.getByText("958")).toBeTruthy();

    const change = item("Checked a change before committing");
    expect(within(change).getByText(/214 rated high risk/)).toBeTruthy();
    expect(within(change).getByText("high 214")).toBeTruthy();
    expect(within(change).getByText("Claude Code has it")).toBeTruthy();
    expect(within(change).getByText("Cursor doesn't")).toBeTruthy();
    expect(within(change).getByText("Add it for Cursor").getAttribute("href")).toBe("/agents");
    // The link shows once; later rows keep only the quiet marks.
    expect(screen.getAllByText("Add it for Cursor")).toHaveLength(1);
    expect(within(item("Checked how risky a file is")).getByText("Cursor doesn't")).toBeTruthy();
    // No dollars or tokens on a check.
    expect(change.textContent).not.toMatch(/\$|tokens/);
  });

  it("offers the sample ask and Copy prompt for a tool that is listed but unused", () => {
    render(<SavingsChecks rows={RECORDED} agents={[claude, cursor]} profiles={PROFILES} copy={copy} />);
    const dead = item("Looked for unused code");
    expect(within(dead).getByText(/Not used yet\./)).toBeTruthy();
    expect(within(dead).getByText(CHECK_PROMPTS.get_dead_code)).toBeTruthy();
    expect(within(dead).getByRole("button", { name: /Copy prompt/ })).toBeTruthy();
  });

  it("says which connected agent lacks a tool when none of them has it, linking once", () => {
    const rows = [row("get_why", 83, { outcome_calls: 83, decisions_accepted: 31, decisions_none: 52 }), ...["get_change_risk", "get_risk", "get_health", "get_dead_code"].map((t) => row(t, 0))];
    render(<SavingsChecks rows={rows} agents={[cursor]} profiles={PROFILES} copy={copy} agentsHref="/agents" />);
    const change = item("Checked a change before committing");
    expect(within(change).getByText(/Cursor doesn't have this tool\./)).toBeTruthy();
    expect(within(change).getByText("Not listed to Cursor")).toBeTruthy();
    expect(screen.getAllByText("Add it in Agents & editors")).toHaveLength(1);
    expect(within(change).queryByRole("button", { name: /Copy prompt/ })).toBeNull();
    // The used tool is listed first.
    const names = screen.getAllByRole("listitem").map((li) => li.querySelector("code")?.textContent);
    expect(names[0]).toBe("get_why");
    // The table's prompt switches the lacking agent's profile.
    expect(screen.getByText(copy.switchHint)).toBeTruthy();
  });

  it("shows call counts with no outcome words for calls recorded before outcomes", () => {
    const rows = RECORDED.map((r) => row(r.tool, r.calls ?? 0));
    render(<SavingsChecks rows={rows} agents={[claude]} profiles={PROFILES} copy={copy} />);
    expect(screen.queryByText("Risky changes caught")).toBeNull();
    // Said once under the intro, not on every row.
    expect(screen.getAllByText(copy.notRecorded)).toHaveLength(1);
    expect(within(item("Checked code health")).queryByText(copy.notRecorded)).toBeNull();
    expect(screen.getByText(copy.oldNote)).toBeTruthy();
    expect(screen.queryByText("See decisions")).toBeNull();
  });

  it("shows one line and a connect link when no agent is connected and nothing was called", () => {
    const rows = RECORDED.map((r) => row(r.tool, 0));
    render(<SavingsChecks rows={rows} agents={[]} copy={copy} connectHref="/setup" />);
    expect(screen.getByText(/No agent is connected yet\./)).toBeTruthy();
    expect(screen.getByText("Connect an agent").getAttribute("href")).toBe("/setup");
    expect(screen.queryByRole("listitem")).toBeNull();
  });

  it("names no agent when the host cannot tell which are connected", () => {
    render(<SavingsChecks rows={RECORDED} copy={copy} />);
    expect(screen.queryByText(/has it|doesn't/)).toBeNull();
    expect(screen.queryByText("Your agents' tools")).toBeNull();
  });

  it("lists each agent's profile, tools and last call", () => {
    render(<SavingsChecks rows={RECORDED} agents={[claude, cursor]} profiles={PROFILES} copy={copy} agentsHref="/agents" />);
    const table = screen.getByRole("table");
    expect(within(table).getByText("standard · 10")).toBeTruthy();
    expect(within(table).getByText("core · 5")).toBeTruthy();
    expect(within(table).getByText("No calls yet")).toBeTruthy();
    expect(screen.getByText(/get the 5 core tools by default/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "Change tools in Agents & editors" }).getAttribute("href")).toBe("/agents");
  });

  it("links outcome rows only to existing pages", () => {
    render(
      <SavingsChecks rows={RECORDED} copy={copy} links={{ get_why: "/decisions", get_health: "/overview" }} />,
    );
    expect(screen.getByText("See decisions").getAttribute("href")).toBe("/decisions");
    expect(screen.getByText("Open in Do next").getAttribute("href")).toBe("/overview");
    expect(screen.queryByText("Open Code Health")).toBeNull();
  });
});

describe("check helpers", () => {
  it("splits connected agents by whether a tool is listed to them", () => {
    const split = toolAvailability("get_risk", [claude, cursor]);
    expect(split.with.map((a) => a.id)).toEqual(["claude-code"]);
    expect(split.without.map((a) => a.id)).toEqual(["cursor"]);
  });

  it("knows when outcomes exist and fills the switch prompt", () => {
    expect(checksHaveOutcomes(RECORDED)).toBe(true);
    expect(checksHaveOutcomes([row("get_why", 3)])).toBe(false);
    expect(switchProfilePrompt("Cursor")).toContain("restart Cursor");
  });
});

describe("SavingsPageBody", () => {
  const usage = { summary: undefined, rows: undefined, grouping: "operation" as const, onGroupingChange: () => {} };
  const savings: SavingsView = {
    available: true,
    unique_events: 10,
    saved_input_tokens: 1000,
    per_operation: [{ group: "get_why", events: 10, saved_input_tokens: 1000, priced_input_savings_usd: 0 }],
  };

  it("places the checks between where it came from and what Repowise used", () => {
    render(<SavingsPageBody savings={{ ...savings, checks: RECORDED }} usage={usage} />);
    const headings = screen.getAllByRole("heading", { level: 2 }).map((h) => h.textContent);
    const at = (text: string) => headings.indexOf(text);
    expect(at("Checks your agents ran")).toBeGreaterThan(at("Where it came from"));
    expect(at("What Repowise used")).toBeGreaterThan(at("Checks your agents ran"));
  });

  it("leaves the section out when the report carries no check rows", () => {
    render(<SavingsPageBody savings={savings} usage={usage} />);
    expect(screen.queryByText("Checks your agents ran")).toBeNull();
  });
});

describe("summary tiles", () => {
  it("shows a tile only when its own tools recorded outcomes", () => {
    const rows = [
      row("get_why", 83, { outcome_calls: 83, decisions_accepted: 31, decisions_candidate: 0, decisions_none: 52 }),
      ...["get_change_risk", "get_risk", "get_health", "get_dead_code"].map((t) => row(t, 0)),
    ];
    render(<SavingsChecks rows={rows} copy={copy} />);
    expect(screen.getByText("Decisions surfaced")).toBeTruthy();
    expect(screen.queryByText("Risky changes caught")).toBeNull();
    expect(screen.queryByText("Problems pointed out")).toBeNull();
  });
});

describe("partly recorded windows", () => {
  it("keeps the per-row line on rows without outcomes when others have them", () => {
    const rows = [
      row("get_change_risk", 10, { outcome_calls: 10, high: 1, medium: 2, low: 7 }),
      row("get_why", 5),
      ...["get_risk", "get_health", "get_dead_code"].map((t) => row(t, 0)),
    ];
    render(<SavingsChecks rows={rows} copy={copy} />);
    expect(within(item("Looked up why code is the way it is")).getByText(copy.notRecorded)).toBeTruthy();
    expect(screen.getAllByText(copy.notRecorded)).toHaveLength(1);
  });
});
