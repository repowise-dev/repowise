/**
 * Fix first renders core's queue as it arrives: the header states the scope
 * exactly, the lead opens with its steps and Verify, and actions appear only
 * where the item can back them.
 */

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { FixFirstQueue, FixItem } from "@repowise-dev/types/fix-first";

import { FixFirstList } from "../../src/health/fix-first/fix-first-list";
import { fixFirstScopeSentence, fixPlanLabel } from "../../src/health/fix-first/scope";
import { buildFixItemPrompt } from "../../src/health/ai-prompts/fix-first-prompt";
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

describe("Fix first header", () => {
  it("states shown of eligible and every nonzero exclusion with its count", () => {
    expect(fixFirstScopeSentence(FIX_FIRST_QUEUE)).toBe(
      "3 of 423 eligible items. Excluded: 612 in tests, 91 tooling, 5 generated or vendored, " +
        "158 expected repetition, 12 with no safe fix, 505 below the worth floor, 167 history only.",
    );
  });

  it("omits a rule that excluded nothing", () => {
    const queue: FixFirstQueue = {
      ...FIX_FIRST_QUEUE,
      totals: {
        ...FIX_FIRST_QUEUE.totals,
        excluded: { ...FIX_FIRST_QUEUE.totals.excluded, test: 0, generated: 0 },
      },
    };
    const sentence = fixFirstScopeSentence(queue);
    expect(sentence).not.toContain("in tests");
    expect(sentence).not.toContain("generated");
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

  it("opens the lead with steps marked mechanical, Verify with reasons, and a copyable command", () => {
    renderList();
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
    fireEvent.click(screen.getByRole("button", { name: new RegExp(FINDING.title.replace(/[()]/g, "\\$&")) }));
    expect(screen.getByText(/No guarding tests found/)).toBeTruthy();
  });

  it("names the plan by its unit, and offers none for a bare finding", () => {
    expect(fixPlanLabel(REFACTOR)).toBe("Open the refactoring plan");
    expect(fixPlanLabel(PERF)).toBe("Open the performance fix");
    expect(fixPlanLabel(FINDING)).toBeNull();
    renderList();
    expect(screen.getByRole("link", { name: "Open the refactoring plan" }).getAttribute("href")).toBe(
      "/plan/refop3_8ba13d978db910d160b1",
    );
  });

  it("offers triage only for an item backed by a finding id, and writes through the host", async () => {
    const onTriage = vi.fn().mockResolvedValue(undefined);
    renderList({ onTriage });
    fireEvent.click(screen.getByRole("button", { name: "Acknowledged" }));
    await waitFor(() => expect(onTriage).toHaveBeenCalledWith(REFACTOR, "acknowledged"));
    expect(await screen.findByText("Saved as acknowledged.")).toBeTruthy();

    // The perf item maps to no finding, so it has no triage control.
    fireEvent.click(screen.getByRole("button", { name: new RegExp(PERF.title) }));
    expect(screen.getAllByRole("button", { name: "Acknowledged" }).length).toBe(1);
  });

  it("says why nothing is listed when the queue is empty", () => {
    renderList({
      queue: { ...FIX_FIRST_QUEUE, items: [], lead: null, totals: { ...FIX_FIRST_QUEUE.totals, shown: 0 } },
    });
    expect(screen.getByText(/Nothing eligible to fix first/)).toBeTruthy();
  });
});

describe("Fix first agent prompt", () => {
  it("carries the steps and the Verify block", () => {
    const prompt = buildFixItemPrompt({ item: REFACTOR, flavor: "claude-code-mcp" });
    expect(prompt).toContain("## Verify");
    expect(prompt).toContain(`Run: \`${REFACTOR.verify.command}\``);
    expect(prompt).toContain("[mechanical]");
    expect(prompt).toContain('get_health(opportunity_id="refop3_8ba13d978db910d160b1")');
  });

  it("tells the agent to pin behaviour when no test guards the change", () => {
    const prompt = buildFixItemPrompt({ item: FINDING });
    expect(prompt).toContain("No guarding tests found.");
  });
});
