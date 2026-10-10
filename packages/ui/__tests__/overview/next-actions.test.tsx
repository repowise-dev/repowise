import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ActionsResponse, NextAction } from "@repowise-dev/types/actions";

import { NextActions } from "../../src/overview/next-actions.js";
import { actionHref } from "../../src/overview/action-href.js";

function action(overrides: Partial<NextAction> = {}): NextAction {
  return {
    id: "act_1",
    rule: "fragile_file",
    tier: "plan",
    horizons: ["quarter"],
    severity: "high",
    title: "Raise test coverage on `src/a.py` from 62%",
    impact: "Changed 30 times in 90 days, and 9 of those commits were bug fixes.",
    why: [
      { label: "commits in 90 days", value: "30", basis: "measured" },
      { label: "line coverage", value: "Unknown, no report", basis: "unknown" },
    ],
    target: { kind: "file", path: "src/a.py", symbol: null },
    surface: "file",
    effort: "M",
    confidence: "high",
    value: 3,
    priority: 1.5,
    done_when: "Line coverage on this file reaches 80%.",
    command: null,
    marker: null,
    evidence_ids: [],
    evidence_total: 0,
    details: [],
    details_total: 0,
    commands: [],
    includes: [],
    fingerprint: "fp1",
    ...overrides,
  };
}

function response(week: NextAction[], quarter: NextAction[]): ActionsResponse {
  const horizon = (actions: NextAction[]) => ({
    actions,
    total: actions.length,
    hidden: 0,
    by_tier: actions.reduce<Record<string, number>>((acc, a) => {
      acc[a.tier] = (acc[a.tier] ?? 0) + 1;
      return acc;
    }, {}),
  });
  return {
    status: "available",
    anchor: "2026-09-28T09:45:08",
    week_start: "2026-09-21T09:45:08",
    context: {
      production_files: 100,
      active_authors_90d: 2,
      fix_commits_90d: 40,
      busy_threshold: 8,
      coverage: "unknown",
      history_too_short: false,
    },
    horizons: { week: horizon(week), quarter: horizon(quarter) },
    rules: [
      { rule: "fragile_file", status: "evaluated", reason: "", emitted: 1 },
      {
        rule: "knowledge_loss",
        status: "not_applicable",
        reason: "2 active authors in 90 days.",
        emitted: 0,
      },
    ],
    unavailable: {},
  };
}

describe("NextActions", () => {
  it("sets paths in mono and leads with the time frame that has work", () => {
    render(<NextActions data={response([], [action()])} hrefFor={() => "/x"} />);
    const row = screen.getByRole("listitem", { name: /Open Raise test coverage/ });
    expect(within(row).getByText("src/a.py").tagName).toBe("CODE");
    expect(screen.getByRole("radio", { name: /This quarter/ })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    expect(screen.getByText("Unknown, no report")).toBeInTheDocument();
  });

  it("switches horizon with the segmented control", () => {
    const now = action({ id: "act_now", tier: "act_now", severity: "critical", horizons: ["week"], title: "Clean up `b.ts`" });
    render(<NextActions data={response([now], [action()])} hrefFor={() => null} />);
    expect(screen.getByText("Now")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: /This quarter/ }));
    expect(screen.getByText("Worth planning")).toBeInTheDocument();
    expect(screen.queryByText("Now")).not.toBeInTheDocument();
  });

  it("explains an empty time frame instead of rendering nothing", () => {
    render(<NextActions data={response([], [])} hrefFor={() => null} />);
    expect(screen.getByText(/none of them produced work/)).toBeInTheDocument();
    expect(screen.getByText("2 active authors in 90 days.")).toBeInTheDocument();
  });

  it("removes a dismissed row at once and sends its fingerprint", async () => {
    const onSetState = vi.fn().mockResolvedValue(undefined);
    render(
      <NextActions data={response([], [action()])} hrefFor={() => null} onSetState={onSetState} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /More for Raise test coverage/ }));
    fireEvent.click(await screen.findByText("Dismiss"));
    expect(screen.queryByText(/Raise test coverage/)).not.toBeInTheDocument();
    await waitFor(() => expect(onSetState).toHaveBeenCalledTimes(1));
    expect(onSetState.mock.calls[0]?.[1]).toBe("dismissed");
    expect(onSetState.mock.calls[0]?.[0].fingerprint).toBe("fp1");
  });

  it("still says everything is answered once the re-read view drops the row", async () => {
    const onSetState = vi.fn().mockResolvedValue(undefined);
    const before = response([], [action()]);
    const { rerender } = render(
      <NextActions data={before} hrefFor={() => null} onSetState={onSetState} />,
    );
    const chip = () => screen.getByRole("radio", { name: /This quarter/ });
    expect(chip()).toHaveTextContent("1");
    fireEvent.click(screen.getByRole("button", { name: /More for Raise test coverage/ }));
    fireEvent.click(await screen.findByText("Dismiss"));
    // Until the re-read lands, the count stays with the view, as the sentence does.
    expect(chip()).toHaveTextContent("1");
    await waitFor(() => expect(onSetState).toHaveBeenCalledTimes(1));

    const after = response([], []);
    after.horizons.quarter.hidden = 1;
    rerender(<NextActions data={after} hrefFor={() => null} onSetState={onSetState} />);
    expect(screen.getByText(/You have answered everything listed here/)).toBeInTheDocument();
    expect(screen.queryByText(/none of them produced work/)).toBeNull();
    expect(chip()).toHaveTextContent("0");
    // The other time frame held nothing to answer, so it reads as empty.
    fireEvent.click(screen.getByRole("radio", { name: /This week/ }));
    expect(screen.getByText(/none of them produced work/)).toBeInTheDocument();
  });

  it("brings a row back when the write fails", async () => {
    const onSetState = vi.fn().mockRejectedValue(new Error("nope"));
    render(
      <NextActions data={response([], [action()])} hrefFor={() => null} onSetState={onSetState} />,
    );
    fireEvent.click(screen.getByRole("button", { name: /More for Raise test coverage/ }));
    fireEvent.click(await screen.findByText("Snooze for 14 days"));
    await waitFor(() => expect(screen.getByText(/Raise test coverage/)).toBeInTheDocument());
  });

  it("renders nothing for a server that predates actions", () => {
    const { container } = render(<NextActions data={null} hrefFor={() => null} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe("the action drawer", () => {
  it("opens on a row click with the evidence link, the facts and the prompt", async () => {
    const loadPrompt = vi.fn().mockResolvedValue("Prompt from core");
    render(
      <NextActions
        data={response([], [action()])}
        hrefFor={() => "/repos/r/files/src/a.py"}
        loadPrompt={loadPrompt}
      />,
    );
    fireEvent.click(screen.getByRole("listitem", { name: /Open Raise test coverage/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("link", { name: /Open the file/ })).toHaveAttribute(
      "href",
      "/repos/r/files/src/a.py",
    );
    expect(within(dialog).getByText("Why it is on the list")).toBeInTheDocument();
    expect(within(dialog).getByText("Not measured.")).toBeInTheDocument();
    expect(await within(dialog).findByText("Prompt from core")).toBeInTheDocument();
    expect(loadPrompt).toHaveBeenCalledWith(action(), expect.any(String));
    expect(within(dialog).getByRole("button", { name: /Copy prompt/ })).toBeEnabled();
  });

  it("leaves the prompt out when the host cannot load one", async () => {
    render(<NextActions data={response([], [action()])} hrefFor={() => null} />);
    fireEvent.click(screen.getByRole("listitem", { name: /Open Raise test coverage/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByText("Hand it to an agent")).toBeNull();
    expect(within(dialog).queryByRole("button", { name: /Copy prompt/ })).toBeNull();
  });
});

describe("the status sentence", () => {
  it("renders the sentence core wrote for the open time frame", () => {
    const data = {
      ...response([], [action()]),
      summary: { week: "Core wording, week.", quarter: "Core wording, quarter." },
    };
    render(<NextActions data={data} hrefFor={() => null} />);
    expect(screen.getByText("Core wording, quarter.")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: /This week/ }));
    expect(screen.getByText("Core wording, week.")).toBeInTheDocument();
  });

  it("shows no sentence from a server that predates it", () => {
    render(<NextActions data={response([], [action()])} hrefFor={() => null} />);
    expect(screen.queryByText(/worth doing|Nothing/)).toBeNull();
  });
});

describe("actionHref", () => {
  it("routes each surface to the page that holds its evidence", () => {
    expect(actionHref(action(), "/repos/r")).toBe("/repos/r/files/src/a.py");
    expect(
      actionHref(action({ surface: "performance", evidence_ids: ["perf2_x"] }), "/repos/r"),
    ).toBe("/repos/r/code-health?tab=performance&opportunity=perf2_x");
    expect(
      actionHref(
        action({ surface: "decisions", target: { kind: "decision", path: "d1", symbol: null } }),
        "/repos/r",
      ),
    ).toBe("/repos/r/decisions/d1");
  });
});

describe("the fix_first rule", () => {
  // Shaped the way core's `fix_first` rule emits a Fix-first item: title and
  // why verbatim, the gain first among the facts, the steps as evidence.
  const fixFirst = action({
    id: "act_fix",
    rule: "fix_first",
    tier: "act_now",
    horizons: ["week", "quarter"],
    title: "Extract lines 60-122 of quick_repo_scan into compute_info",
    impact: "quick_repo_scan: CCN 15, 44 lines, nests 3 deep; 4 files import it, changed 5 times in 90 days.",
    why: [
      { label: "gain", value: "+1.7 health on this file", basis: "inferred" },
      { label: "files that import it", value: "4", basis: "measured" },
    ],
    target: {
      kind: "symbol",
      path: "packages/cli/src/repowise/cli/ui/repo_scanner.py",
      symbol: "quick_repo_scan",
    },
    surface: "findings",
    effort: "S",
    done_when: "It leaves Fix first on the next update.",
    details: [
      {
        path: "packages/cli/src/repowise/cli/ui/repo_scanner.py",
        line: 60,
        symbol: null,
        marker: null,
        severity: null,
        reason: "Extract lines 60-122 of quick_repo_scan into compute_info(repo_path) -> info",
        ref: null,
      },
    ],
    details_total: 1,
    commands: [
      {
        purpose: "The full item: steps, tests to run, risk",
        mcp: 'get_health(fix_id="fix1_2b0e5c0acbb469f46aa9")',
        cli: "repowise health",
      },
    ],
  });

  it("renders title, file:line and why on the row", () => {
    render(<NextActions data={response([fixFirst], [])} hrefFor={() => null} />);
    const row = screen.getByRole("listitem", { name: /Open Extract lines 60-122/ });
    expect(within(row).getByText("packages/cli/src/repowise/cli/ui/repo_scanner.py:60")).toBeInTheDocument();
    expect(within(row).getByText(/CCN 15, 44 lines/)).toBeInTheDocument();
    expect(within(row).getByText("+1.7 health on this file")).toBeInTheDocument();
  });

  it("hands the item to the host's prompt, and no hot-path copy remains", async () => {
    const loadPrompt = vi.fn().mockResolvedValue("Extract lines 60-122 of quick_repo_scan");
    render(<NextActions data={response([fixFirst], [])} hrefFor={() => null} loadPrompt={loadPrompt} />);
    fireEvent.click(screen.getByRole("listitem", { name: /Open Extract lines 60-122/ }));
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByText("Extract lines 60-122 of quick_repo_scan");
    expect(loadPrompt).toHaveBeenCalledWith(fixFirst, expect.any(String));
    expect(document.body.textContent).not.toMatch(/hot path/i);
  });

  it("adds no location line when the title already names the file", () => {
    render(<NextActions data={response([], [action()])} hrefFor={() => null} />);
    const row = screen.getByRole("listitem", { name: /Open Raise test coverage/ });
    expect(within(row).queryByText("src/a.py:")).toBeNull();
    expect(within(row).getAllByText("src/a.py").length).toBe(1);
  });
});

describe("host-configurable hints", () => {
  const unavailable = (): ActionsResponse => ({
    ...response([], [action()]),
    unavailable: { coverage: "no report" },
  });
  const withCli = () =>
    action({
      commands: [
        { purpose: "See the file", cli: "repowise get-risk src/a.py", mcp: "get_risk(src/a.py)" },
      ],
    } as Partial<NextAction>);

  it("shows the repowise update hint by default", () => {
    render(<NextActions data={unavailable()} hrefFor={() => null} />);
    expect(screen.getByText("repowise update").tagName).toBe("CODE");
    expect(screen.getByText(/to include them\./)).toBeInTheDocument();
  });

  it("hides the hint sentence when unavailableHint is null", () => {
    render(<NextActions data={unavailable()} hrefFor={() => null} unavailableHint={null} />);
    expect(screen.getByText(/Not checked: coverage\./)).toBeInTheDocument();
    expect(screen.queryByText("repowise update")).toBeNull();
  });

  it("renders a custom hint in place of the default", () => {
    render(
      <NextActions data={unavailable()} hrefFor={() => null} unavailableHint="Ask your admin." />,
    );
    expect(screen.getByText(/Ask your admin\./)).toBeInTheDocument();
    expect(screen.queryByText("repowise update")).toBeNull();
  });

  it("shows CLI command lines by default and hides them with showCliCommands=false", async () => {
    const data = response([], [withCli()]);
    const { unmount } = render(<NextActions data={data} hrefFor={() => null} />);
    fireEvent.click(screen.getByRole("listitem", { name: /Open Raise test coverage/ }));
    expect(await screen.findByText("repowise get-risk src/a.py")).toBeInTheDocument();
    unmount();
    render(<NextActions data={data} hrefFor={() => null} showCliCommands={false} />);
    fireEvent.click(screen.getByRole("listitem", { name: /Open Raise test coverage/ }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByText("repowise get-risk src/a.py")).toBeNull();
    expect(within(dialog).getByText(/get_risk\(src\/a\.py\)/)).toBeInTheDocument();
  });
});
