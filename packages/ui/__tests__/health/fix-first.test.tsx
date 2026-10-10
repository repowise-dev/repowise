/**
 * Fix first renders core's queue as it arrives: the section starts open
 * with its count, the header states the scope exactly, exclusions sit in a
 * popover, each row is two scan
 * lines that open onto the why, steps and Verify, and actions appear only
 * where the item can back them.
 */

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { FixFirstQueue, FixItem } from "@repowise-dev/types/fix-first";

import { FixFirstList } from "../../src/health/fix-first/fix-first-list";
import {
  exclusionPhrase,
  fixFirstScopeSentence,
  fixPlanLabel,
} from "../../src/health/fix-first/scope";
import { FIX_FIRST_QUEUE } from "./fixtures/fix-first";

const [REFACTOR, FINDING, PERF] = FIX_FIRST_QUEUE.items as [FixItem, FixItem, FixItem];

function renderList(overrides: Partial<React.ComponentProps<typeof FixFirstList>> = {}) {
  return render(
    <FixFirstList
      queue={FIX_FIRST_QUEUE}
      scope="production"
      onScopeChange={() => {}}
      fileHref={(path) => `/files/${path}`}
      planHref={(item) => (item.source.opportunity_id ? `/plan/${item.source.opportunity_id}` : null)}
      {...overrides}
    />,
  );
}

/** Rows start closed; open one by its title. */
function openRow(item: FixItem) {
  fireEvent.click(screen.getByRole("button", { name: new RegExp(item.title.replace(/[()]/g, "\\$&")) }));
}

describe("Fix first header", () => {
  it("starts open, with the title and the item count on the toggle", () => {
    const { container } = renderList();
    const details = container.querySelector("details")!;
    expect(details.open).toBe(true);
    const summary = container.querySelector("summary")!;
    expect(summary.textContent).toContain("Fix first");
    expect(summary.textContent).toContain("3 items");
  });

  it("states shown of eligible, and lists every nonzero exclusion in a popover", async () => {
    expect(fixFirstScopeSentence(FIX_FIRST_QUEUE)).toBe("3 of 423 eligible items.");
    renderList();
    expect(screen.queryByText("612 in tests")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "1,550 excluded" }));
    const dialog = await screen.findByRole("dialog");
    for (const line of [
      "612 in tests",
      "91 tooling",
      "5 generated",
      "158 expected repetition",
      "12 with no safe fix",
      "505 below the worth floor",
      "167 history only",
    ]) {
      expect(within(dialog).getByText(line)).toBeTruthy();
    }
  });

  it("counts dormant functions apart from what they excluded", () => {
    const queue: FixFirstQueue = {
      ...FIX_FIRST_QUEUE,
      totals: {
        ...FIX_FIRST_QUEUE.totals,
        excluded: { ...FIX_FIRST_QUEUE.totals.excluded, gated_off: 3, unreachable: 2 },
        dormant: 1,
      },
    };
    expect(exclusionPhrase(queue.totals.excluded)).toContain(
      "3 switched off by a constant flag, 2 in dead code (delete it)",
    );
    expect(fixFirstScopeSentence(queue)).toMatch(/Dormant: 1 function behind a disabled flag\.$/);
  });

  it("omits a rule that excluded nothing", () => {
    const queue: FixFirstQueue = {
      ...FIX_FIRST_QUEUE,
      totals: {
        ...FIX_FIRST_QUEUE.totals,
        excluded: { ...FIX_FIRST_QUEUE.totals.excluded, test: 0, generated: 0 },
      },
    };
    const phrase = exclusionPhrase(queue.totals.excluded);
    expect(phrase).not.toContain("in tests");
    expect(phrase).not.toContain("generated");
  });

  it("asks for scope=all when tests are included, and says so", () => {
    const onScopeChange = vi.fn();
    const { rerender } = renderList({ onScopeChange });
    fireEvent.click(screen.getByLabelText("Include tests"));
    expect(onScopeChange).toHaveBeenCalledWith("all");
    rerender(
      <FixFirstList queue={FIX_FIRST_QUEUE} scope="all" onScopeChange={onScopeChange} />,
    );
    expect(screen.getByText(/Test files are included\./)).toBeTruthy();
  });
});

describe("Fix first items", () => {
  it("renders tier as a word, the imperative title, and file:line linking to the file view", () => {
    renderList();
    const closed = screen.getAllByRole("listitem").filter((li) => li.parentElement?.tagName === "OL")[0]!;
    // Closed, the row is tier, title, file:line and gain; the rest waits behind it.
    expect(within(closed).getByText("+1.7 health on this file")).toBeTruthy();
    expect(within(closed).queryByText(REFACTOR.why)).toBeNull();
    openRow(REFACTOR);
    const items = screen.getAllByRole("listitem").filter((li) => li.parentElement?.tagName === "OL");
    const lead = items[0]!;
    expect(within(lead).getByText("Now")).toBeTruthy();
    expect(within(lead).getByText(REFACTOR.title)).toBeTruthy();
    // The header location and the one step share a span, so both link the file.
    const [link] = within(lead).getAllByRole("link", {
      name: "packages/cli/src/repowise/cli/ui/repo_scanner.py:60",
    });
    expect(link!.getAttribute("href")).toBe("/files/packages/cli/src/repowise/cli/ui/repo_scanner.py");
    expect(within(lead).getByText(REFACTOR.why)).toBeTruthy();
    expect(within(lead).getByText("+1.7 health on this file")).toBeTruthy();
    expect(within(lead).getByText("Small")).toBeTruthy();
  });

  it("opens a row onto steps marked mechanical, Verify with reasons, and a copyable command", () => {
    renderList();
    openRow(REFACTOR);
    expect(screen.getByText(/Extract lines 60-122 of quick_repo_scan into compute_info\(repo_path\)/)).toBeTruthy();
    expect(screen.getAllByText("Mechanical").length).toBe(1);
    expect(screen.getByText("tests/unit/cli/test_repo_scanner.py")).toBeTruthy();
    expect(
      screen.getAllByText(": reaches the changed code through the call-graph").length,
    ).toBe(4);
    expect(screen.getByText(REFACTOR.verify.command!)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy command" })).toBeTruthy();
  });

  it("says no guarding tests were found when Verify is empty", () => {
    renderList();
    openRow(FINDING);
    expect(screen.getByText(/No guarding tests found/)).toBeTruthy();
  });

  it("shows a due item's first step on the closed row, and the plan's own words when open", () => {
    const first = "No test reaches this; add a characterization test for `run` before the edit.";
    const step = {
      ...REFACTOR.action.steps[0]!,
      signature: "def _<name>(repo_path):",
      call: "info = _<name>(repo_path)",
      command: "pytest tests/unit/cli/test_repo_scanner.py",
    };
    const item: FixItem = {
      ...REFACTOR,
      action: { ...REFACTOR.action, steps: [step] },
      verify: { tests: [], tests_total: 0, command: null, basis: "unknown", prerequisite: first },
    };
    renderList({ queue: { ...FIX_FIRST_QUEUE, items: [item], lead: item } });
    expect(screen.getByText(step.text)).toBeTruthy();
    openRow(item);
    // Open, the line gives way to the step itself, with what it writes and checks.
    expect(screen.getAllByText(step.text).length).toBe(1);
    expect(screen.getByText("New helper")).toBeTruthy();
    expect(screen.getByText("info = _<name>(repo_path)")).toBeTruthy();
    expect(screen.getByText(/with a name for what the lines do/)).toBeTruthy();
    expect(screen.getByText(step.command)).toBeTruthy();
    expect(screen.getByText(first)).toBeTruthy();
  });

  it("keeps a later item's closed row to two lines", () => {
    const later: FixItem = { ...FINDING, tier: "later" };
    renderList({ queue: { ...FIX_FIRST_QUEUE, items: [later], lead: later } });
    expect(screen.queryByText("First step")).toBeNull();
    expect(screen.queryByText(later.action.steps[0]!.text)).toBeNull();
  });

  it("names the plan by its unit, and offers none for a bare finding", () => {
    expect(fixPlanLabel(REFACTOR)).toBe("Open the refactoring plan");
    expect(fixPlanLabel(PERF)).toBe("Open the performance fix");
    expect(fixPlanLabel(FINDING)).toBeNull();
    renderList();
    openRow(REFACTOR);
    expect(screen.getByRole("link", { name: "Open the refactoring plan" }).getAttribute("href")).toBe(
      "/plan/refop3_8ba13d978db910d160b1",
    );
  });

  it("offers triage only for an item backed by a finding id, and writes through the host", async () => {
    const onTriage = vi.fn().mockResolvedValue(undefined);
    renderList({ onTriage });
    openRow(REFACTOR);
    fireEvent.click(screen.getByRole("button", { name: "Acknowledged" }));
    await waitFor(() => expect(onTriage).toHaveBeenCalledWith(REFACTOR, "acknowledged"));
    expect(await screen.findByText("Saved as acknowledged.")).toBeTruthy();

    // The perf item maps to no finding, so it has no triage control.
    openRow(PERF);
    expect(screen.getAllByRole("button", { name: "Acknowledged" }).length).toBe(1);
  });

  it("separates excluded-by-rule, no analysis and all clear when the queue is empty", () => {
    const empty = { ...FIX_FIRST_QUEUE, items: [], lead: null };
    const { unmount } = renderList({
      queue: { ...empty, totals: { ...empty.totals, shown: 0 } },
    });
    expect(screen.getByText("Everything was left out by a rule")).toBeTruthy();
    unmount();

    const noExclusions = Object.fromEntries(
      Object.keys(empty.totals.excluded).map((k) => [k, 0]),
    ) as typeof empty.totals.excluded;
    const none = { ...empty, totals: { ...empty.totals, shown: 0, excluded: noExclusions } };
    const second = renderList({
      queue: { ...none, basis: { analyzed_commit: null, health_analyzed_at: null } },
    });
    expect(screen.getByText("Health has not been analyzed yet")).toBeTruthy();
    second.unmount();

    renderList({
      queue: { ...none, basis: { analyzed_commit: "abc", health_analyzed_at: "2026-10-01" } },
    });
    expect(screen.getByText("Nothing to fix first")).toBeTruthy();
  });
});
