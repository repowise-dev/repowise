import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ActionsResponse, NextAction } from "@repowise-dev/types/actions";

import { NextActions, actionsStatus } from "../../src/overview/next-actions.js";
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
    done_when: "Line coverage on this file reaches 80%.",
    command: null,
    marker: null,
    evidence_ids: [],
    evidence_total: 0,
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
    const title = screen.getByRole("link", { name: /Raise test coverage/ });
    expect(within(title).getByText("src/a.py").tagName).toBe("CODE");
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

describe("actionsStatus", () => {
  it("names the window by its last indexed commit and counts work only", () => {
    const now = action({ tier: "act_now", horizons: ["week"] });
    const signal = action({ id: "s", tier: "improve_signal", horizons: ["week"] });
    const text = actionsStatus(response([now, signal], []), "week");
    expect(text).toMatch(/^1 thing worth doing in the week to /);
    expect(text).toMatch(/1 of them now\.$/);
  });

  it("points to the other time frame when this one is clear", () => {
    expect(actionsStatus(response([], [action()]), "week")).toBe(
      "Nothing needs you in the week to " +
        new Date("2026-09-28T09:45:08").toLocaleDateString(undefined, { month: "short", day: "numeric" }) +
        ", the last indexed commit. 1 thing is worth planning this quarter.",
    );
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
