// @vitest-environment jsdom

import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ChangelogEntry } from "@/lib/api/meta";
import { WhatsNewModal } from "./whats-new-modal";

const ENTRIES: ChangelogEntry[] = [
  {
    version: "0.57.0",
    label: "2026-10-20",
    summary: "Chat draws charts (#3860).",
    highlights: [
      { id: "charts-in-chat", title: "Charts in chat", blurb: "Any chart.", href: "/repos/:id/chat" },
      { id: "settings", title: "Models from Settings", blurb: "Live lists.", href: "/settings" },
      { id: "palette", title: "One palette", blurb: "Same colours.", href: null },
    ],
    stats: {
      since: "0.56.0",
      days: 8,
      prs: 1205,
      contributors: 24,
      first_time_contributors: 11,
      files_changed: 1226,
      lines_added: 103206,
      lines_removed: 11127,
      types: { fixes: 147, features: 45, perf: 0, refactors: 4, docs: 6 },
      test_files_touched: 437,
      test_files_new: 144,
      issues_closed: 25,
    },
    sections: [{ name: "Fixed", items: ["A fix (#12)."] }],
  },
  {
    version: "0.56.0",
    label: "2026-10-12",
    summary: "The earlier release.",
    highlights: [],
    stats: null,
    sections: [{ name: "Added", items: ["Old thing."] }],
  },
];

const changelogCalls = vi.hoisted(() => [] as unknown[][]);
vi.mock("@/lib/hooks/use-meta-version", () => ({
  useChangelog: (...args: unknown[]) => {
    changelogCalls.push(args);
    return { entries: ENTRIES, error: null, isLoading: false };
  },
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/repos/r1/overview" }));

describe("WhatsNewModal", () => {
  it("asks for newer releases only when told to", () => {
    changelogCalls.length = 0;
    const { unmount } = render(<WhatsNewModal open onOpenChange={() => {}} />);
    expect(changelogCalls.at(-1)?.[2]).toBe(false);
    unmount();
    render(<WhatsNewModal open onOpenChange={() => {}} version="0.57.0" includeNewer />);
    expect(changelogCalls.at(-1)?.[2]).toBe(true);
  });

  it("leads with the release, its numbers and highlights", () => {
    render(<WhatsNewModal open onOpenChange={() => {}} version="0.57.0" fresh />);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("v0.57.0")).toBeTruthy();
    expect(within(dialog).getByRole("link", { name: "#3860" }).getAttribute("href")).toBe(
      "https://github.com/repowise-dev/repowise/pull/3860",
    );
    // Locale separators, and no contributor names anywhere.
    expect(within(dialog).getByText("1,205")).toBeTruthy();
    expect(within(dialog).getByText("+103,206 / -11,127 lines")).toBeTruthy();
    expect(within(dialog).getByText(/welcome to the 11 who made their first contribution/)).toBeTruthy();
    expect(dialog.textContent).not.toContain("@");
    // Kinds with no commits are left out of the strip.
    expect(within(dialog).getByText(/^45 features, 4 refactors,? and 6 documentation changes$/)).toBeTruthy();
    expect(dialog.textContent).not.toContain("performance");
    // Repo-scoped routes resolve to the repository being read.
    expect(within(dialog).getByRole("link", { name: /^Charts in chat/ }).getAttribute("href")).toBe(
      "/repos/r1/chat",
    );
    expect(within(dialog).getByRole("link", { name: /^Models from Settings/ }).getAttribute("href")).toBe(
      "/settings",
    );
    expect(within(dialog).getAllByText("New")).toHaveLength(3);
  });

  it("keeps every change collapsed until opened", () => {
    render(<WhatsNewModal open onOpenChange={() => {}} version="0.57.0" />);
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).queryByText("New")).toBeNull();
    expect(within(dialog).queryByText(/A fix/)).toBeNull();
    const details = within(dialog).getByText("Fixed").closest("details")!;
    details.open = true;
    fireEvent(details, new Event("toggle"));
    expect(within(dialog).getByText(/A fix/)).toBeTruthy();
  });

  it("switches to an earlier release", () => {
    render(<WhatsNewModal open onOpenChange={() => {}} version="0.57.0" />);
    const dialog = screen.getByRole("dialog");
    fireEvent.click(within(dialog).getByRole("button", { name: /v0\.56\.0/ }));
    expect(within(dialog).getByText("The earlier release.")).toBeTruthy();
    expect(within(dialog).queryByText("By the numbers", { exact: false })).toBeNull();
  });
});
