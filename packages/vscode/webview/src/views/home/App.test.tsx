import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { formatRelativeTime } from "@repowise-dev/ui/lib/format";
import type { HomeSummary } from "../../../../src/shared/webviewMessages";
import type { WebviewHost } from "../../runtime/rpc";
import { App, timeAgo } from "./App";

const SUMMARY: HomeSummary = {
  health: {
    hotspot: 5.1,
    average: 7.4,
    fileCount: 1280,
    openFindings: 42,
    band: "good",
    hotspotDelta: 0.3,
    history: [4.8, 4.9, 5.0, 5.1],
  },
  counts: { refactoringPlans: 12, decisions: 48 },
  freshness: {
    indexedCommit: "abc1234deadbeef",
    liveCommit: "abc1234deadbeef",
    stale: false,
    branch: "main",
    lastIndexedAt: "2026-07-01T10:00:00Z",
  },
};

function makeHost(summary: HomeSummary = SUMMARY) {
  const openView = vi.fn();
  const updateIndex = vi.fn();
  const setTheme = vi.fn();
  let fireUpdateDone: () => void = () => {};
  const host = {
    api: { homeSummary: vi.fn(() => Promise.resolve(summary)) },
    openView,
    updateIndex,
    setTheme,
    onUpdateDone: (cb: () => void) => {
      fireUpdateDone = cb;
      return () => {};
    },
    onThemeChanged: () => () => {},
  } as unknown as WebviewHost;
  return { host, openView, updateIndex, setTheme, fireUpdateDone: () => fireUpdateDone() };
}

function renderApp(host: WebviewHost) {
  return render(
    <App
      host={host}
      repo={{ id: "r1", name: "demo-repo", headCommit: "abc1234", defaultBranch: "main" }}
      params={{}}
      refreshToken={0}
    />,
  );
}

afterEach(cleanup);

describe("Home sidebar", () => {
  it("renders the hero score, freshness, and live card stats", async () => {
    const { host } = makeHost();
    renderApp(host);

    // Average health leads the hero; hotspot health is the inline secondary.
    expect(await screen.findByText("7.4")).toBeTruthy();
    expect(screen.getByText(/hotspot 5\.1/)).toBeTruthy();
    expect(screen.getByText("demo-repo")).toBeTruthy();
    expect(screen.getByText("Index up to date")).toBeTruthy();
    expect(screen.getByText("12 ranked plans")).toBeTruthy();
    expect(screen.getByText("48 decision records")).toBeTruthy();
    expect(screen.getByText(/42 open findings/)).toBeTruthy();
  });

  it("opens the matching panel when a launcher card is clicked", async () => {
    const { host, openView } = makeHost();
    renderApp(host);

    fireEvent.click(await screen.findByText("Code Health"));
    expect(openView).toHaveBeenCalledWith("health");

    fireEvent.click(screen.getByText("Change Risk"));
    expect(openView).toHaveBeenCalledWith("risk");
  });

  it("flags a stale index and runs the update through the host", async () => {
    const { host, updateIndex, fireUpdateDone } = makeHost({
      ...SUMMARY,
      freshness: { ...SUMMARY.freshness, liveCommit: "fff9999aaaa", stale: true },
    });
    renderApp(host);

    expect(await screen.findByText("Index behind checkout")).toBeTruthy();
    expect(screen.getByText("abc1234 → fff9999")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: /Update/ }));
    expect(updateIndex).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Updating index…")).toBeTruthy();

    // The host ack clears the spinner and refetches the summary.
    fireUpdateDone();
    expect(await screen.findByText(/Index/)).toBeTruthy();
    expect((host.api.homeSummary as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(1);
  });

  it("falls back to the average score when hotspot scoring is gated off", async () => {
    const { host } = makeHost({
      ...SUMMARY,
      health: { ...SUMMARY.health!, hotspot: null, hotspotDelta: null },
    });
    renderApp(host);

    expect(await screen.findByText("7.4")).toBeTruthy();
    expect(screen.getByText(/Average health/)).toBeTruthy();
  });

  it("pins the theme through the host and applies it locally", async () => {
    const { host, setTheme } = makeHost();
    renderApp(host);
    await screen.findByText("7.4");

    fireEvent.click(screen.getByTitle("Dark theme"));
    expect(setTheme).toHaveBeenCalledWith("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);

    fireEvent.click(screen.getByTitle("Light theme"));
    expect(setTheme).toHaveBeenCalledWith("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("keeps the launcher usable when the summary fails", async () => {
    const host = {
      api: { homeSummary: () => Promise.reject(new Error("down")) },
      openView: vi.fn(),
      focusHome: vi.fn(),
      openNativeSettings: vi.fn(),
      updateIndex: vi.fn(),
      setTheme: vi.fn(),
      onUpdateDone: () => () => {},
      onThemeChanged: () => () => {},
    } as unknown as WebviewHost;
    renderApp(host);

    expect(await screen.findByText("Knowledge Graph")).toBeTruthy();
    expect(screen.getByText("Server-ranked refactoring opportunities")).toBeTruthy();
    // findByText, not getByText: the launcher renders before the summary
    // promise rejection flushes, so on a slow runner the hero card can still
    // be in its loading skeleton when the launcher assertions pass.
    expect(await screen.findByText(/No health data yet/)).toBeTruthy();
  });
});

describe("timeAgo", () => {
  it("formats relative time using formatRelativeTime and handles invalid/empty", () => {
    const now = Date.now();
    // Fresh times (< 1 min) and future times return "just now"
    expect(timeAgo(new Date(now - 3000).toISOString())).toBe("just now");
    expect(timeAgo(new Date(now - 30000).toISOString())).toBe("just now");
    expect(timeAgo(new Date(now - 55000).toISOString())).toBe("just now");
    expect(timeAgo(new Date(now + 60000).toISOString())).toBe("just now");
    expect(timeAgo(new Date(now - 120000).toISOString())).toBe("2m ago");

    // 20 days ago matches formatRelativeTime ("2w ago")
    const twentyDaysAgo = new Date(now - 20 * 86400 * 1000).toISOString();
    expect(timeAgo(twentyDaysAgo)).toBe(formatRelativeTime(twentyDaysAgo));
    expect(timeAgo(twentyDaysAgo)).toBe("2w ago");

    // 45 days ago matches formatRelativeTime ("1mo ago")
    const fortyFiveDaysAgo = new Date(now - 45 * 86400 * 1000).toISOString();
    expect(timeAgo(fortyFiveDaysAgo)).toBe(formatRelativeTime(fortyFiveDaysAgo));
    expect(timeAgo(fortyFiveDaysAgo)).toBe("1mo ago");

    // Missing or invalid returns ""
    expect(timeAgo("")).toBe("");
    expect(timeAgo(null)).toBe("");
    expect(timeAgo(undefined)).toBe("");
    expect(timeAgo("invalid-date")).toBe("");
  });
});

