// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from "vitest";
import {
  NEW_MARK_DAYS,
  activeNewRoutes,
  decideMoment,
  highlightRepoId,
  rememberRepo,
  linkPullRequests,
  navOwnsRoute,
  previewMoment,
  readNewSurfaces,
  repoIdFromPath,
  resolveHighlightHref,
  routeMatches,
  startNewSurfaces,
  tabOwnsRoute,
  takeUpgradeMoment,
  upgradeMoment,
  visitNewSurface,
} from "./release-moment";
import { GLOBAL_NAV, WORKSPACE_NAV, repoNavItems } from "@/components/layout/nav-items";

const HL = (href: string | null) => ({ id: "x", title: "X", blurb: "", href });

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

describe("upgradeMoment", () => {
  it("shows nothing on a first visit, a repeat, or a downgrade", () => {
    expect(upgradeMoment(null, "0.57.0", true)).toBe("none");
    expect(upgradeMoment("0.57.0", "0.57.0", true)).toBe("none");
    expect(upgradeMoment("0.57.1", "0.57.0", true)).toBe("none");
  });

  it("opens the dialog for a minor release or one with highlights", () => {
    expect(upgradeMoment("0.56.3", "0.57.0", false)).toBe("dialog");
    expect(upgradeMoment("0.57.0", "0.57.1", true)).toBe("dialog");
  });

  it("shows the quiet note for a patch", () => {
    expect(upgradeMoment("0.57.0", "0.57.1", false)).toBe("notice");
  });
});

describe("takeUpgradeMoment", () => {
  it("stores silently on the first visit, then fires once per version", () => {
    expect(takeUpgradeMoment("0.56.0", false)).toBe("none");
    expect(takeUpgradeMoment("0.57.0", true)).toBe("dialog");
    expect(takeUpgradeMoment("0.57.0", true)).toBe("none");
    expect(takeUpgradeMoment("0.57.1", false)).toBe("notice");
    expect(takeUpgradeMoment("0.57.1", false)).toBe("none");
  });
});

describe("browsers from before the last-seen key", () => {
  it("counts earlier repowise state as an upgrade", () => {
    window.localStorage.setItem("repowise:upgrade-banner-dismissed:v0.55.0", "1");
    expect(takeUpgradeMoment("0.57.0", true)).toBe("dialog");
    expect(takeUpgradeMoment("0.57.0", true)).toBe("none");
  });

  it("counts underscore keys too, and a patch gets the note", () => {
    window.localStorage.setItem("repowise_chat_hint_seen", "1");
    expect(takeUpgradeMoment("0.57.1", false)).toBe("notice");
  });

  it("keeps a truly fresh browser quiet", () => {
    window.localStorage.setItem("theme", "dark");
    expect(takeUpgradeMoment("0.57.0", true)).toBe("none");
  });
});

describe("new surfaces", () => {
  it("marks highlight routes until visited", () => {
    startNewSurfaces("0.57.0", [HL("/repos/:id/chat"), HL("/settings"), HL(null)]);
    const now = Date.now();
    expect(activeNewRoutes(readNewSurfaces(), now)).toEqual(["/repos/:id/chat", "/settings"]);
    visitNewSurface("/repos/abc/chat/42");
    expect(activeNewRoutes(readNewSurfaces(), now)).toEqual(["/settings"]);
  });

  it("drops every mark after the window", () => {
    startNewSurfaces("0.57.0", [HL("/settings")]);
    const later = Date.now() + (NEW_MARK_DAYS + 1) * 24 * 60 * 60 * 1000;
    expect(activeNewRoutes(readNewSurfaces(), later)).toEqual([]);
  });

  it("matches routes by segment", () => {
    expect(routeMatches("/repos/:id/chat", "/repos/a/chat")).toBe(true);
    expect(routeMatches("/settings", "/repos/a/settings")).toBe(false);
    expect(routeMatches("/repos/:id/chat", "/repos/a")).toBe(false);
  });
});

// The 0.62.0 Highlights as docs/CHANGELOG.md ships them: the marks below are
// what an upgrade to it shows.
const HIGHLIGHTS_0_62 = `
- **Runtime evidence on the Performance tab** · Store a profile. → /repos/:id/code-health?tab=performance
- **A repo report after the first index** · What Repowise found. → /repos/:id/report
- **Usage & savings, rebuilt** · How much less your agents read. → /repos/:id/costs
- **Faster, sharper agent calls** · First answers in about half the time.
- **Setup that finishes itself** · \`repowise next --prompt setup\` hands the steps to your agent. → /settings/setup
`;

describe("nav marks", () => {
  const routes = [...HIGHLIGHTS_0_62.matchAll(/→ (\/\S*)/g)].map((m) => m[1]!);
  const marked = (items: { label: string; href: string; exact?: boolean }[]) =>
    items.filter((i) => routes.some((r) => navOwnsRoute(r, i.href, i.exact))).map((i) => i.label);

  it("marks the nav entry each 0.62 highlight lives under", () => {
    expect(routes).toHaveLength(4);
    expect(marked(repoNavItems("abc"))).toEqual(["Code Health", "Usage & savings"]);
    expect(marked(GLOBAL_NAV)).toEqual(["Settings"]);
    expect(marked(WORKSPACE_NAV)).toEqual([]);
  });

  it("marks the highlighted tab, and only that one", () => {
    const route = "/repos/:id/code-health?tab=performance";
    expect(tabOwnsRoute(route, "/repos/abc/code-health?tab=performance")).toBe(true);
    expect(tabOwnsRoute(route, "/repos/abc/code-health?tab=findings")).toBe(false);
    expect(tabOwnsRoute("/repos/:id/costs", "/repos/abc/costs?tab=x")).toBe(false);
  });

  it("keeps a tab's mark until that tab is opened", () => {
    startNewSurfaces("0.62.0", [HL("/repos/:id/code-health?tab=performance")]);
    visitNewSurface("/repos/abc/code-health");
    visitNewSurface("/repos/abc/code-health?tab=findings");
    expect(activeNewRoutes(readNewSurfaces(), Date.now())).toHaveLength(1);
    visitNewSurface("/repos/abc/code-health?tab=performance");
    expect(activeNewRoutes(readNewSurfaces(), Date.now())).toEqual([]);
  });

  it("lets neither the root nor an exact entry claim pages beneath it", () => {
    expect(navOwnsRoute("/settings", "/")).toBe(false);
    expect(navOwnsRoute("/workspace/estate", "/workspace", true)).toBe(false);
    expect(navOwnsRoute("/workspace/estate", "/workspace/estate")).toBe(true);
    expect(navOwnsRoute("/repos/:id", "/repos/abc/chat")).toBe(false);
  });
});

describe("links", () => {
  it("resolves repo-scoped routes for the current repository only", () => {
    expect(resolveHighlightHref("/repos/:id/chat", "my repo")).toBe("/repos/my%20repo/chat");
    expect(resolveHighlightHref("/repos/:id/chat", null)).toBeNull();
    expect(resolveHighlightHref("/settings", null)).toBe("/settings");
    expect(repoIdFromPath("/repos/abc/overview")).toBe("abc");
    expect(repoIdFromPath("/settings")).toBeNull();
  });

  it("links PR refs outside code spans", () => {
    expect(linkPullRequests("Charts (#3860, #3843) and `#1`")).toBe(
      "Charts ([#3860](https://github.com/repowise-dev/repowise/pull/3860), " +
        "[#3843](https://github.com/repowise-dev/repowise/pull/3843)) and `#1`",
    );
    expect(linkPullRequests("[#5](x) and a#6")).toBe("[#5](x) and a#6");
  });

  it("reads the preview query", () => {
    expect(previewMoment("?whats-new=preview")).toBe("dialog");
    expect(previewMoment("?whats-new=patch")).toBe("notice");
    expect(previewMoment("?x=1")).toBeNull();
  });
});

describe("decideMoment", () => {
  it("replays a preview without touching what the browser has seen", () => {
    expect(decideMoment("0.57.0", [], "?whats-new=preview")).toEqual({ moment: "dialog", preview: true });
    expect(window.localStorage.getItem("repowise:last-seen-server-version")).toBeNull();
  });

  it("starts the New marks only for a real upgrade", () => {
    window.localStorage.setItem("repowise:last-seen-server-version", "0.56.0");
    expect(decideMoment("0.57.0", [HL("/settings")], "")).toEqual({ moment: "dialog", preview: false });
    expect(readNewSurfaces()?.routes).toEqual(["/settings"]);
  });

  it("stays quiet on a tour load and keeps the upgrade for the next session", () => {
    window.localStorage.setItem("repowise:last-seen-server-version", "0.56.0");
    expect(decideMoment("0.57.0", [], "?tour=1")).toEqual({ moment: "none", preview: false });
    expect(window.localStorage.getItem("repowise:last-seen-server-version")).toBe("0.56.0");
  });
});

describe("highlight repository", () => {
  it("uses the page's repo, else the last opened, else the first listed", () => {
    rememberRepo("/workspace", "first");
    expect(highlightRepoId("/workspace")).toBe("first");
    rememberRepo("/repos/abc/chat", "first");
    expect(highlightRepoId("/workspace")).toBe("abc");
    rememberRepo("/settings", "first");
    expect(highlightRepoId("/settings")).toBe("abc");
    expect(highlightRepoId("/repos/xyz/overview")).toBe("xyz");
  });

  it("is null with no repositories", () => {
    rememberRepo("/", null);
    expect(highlightRepoId("/")).toBeNull();
  });

  it("does not count as earlier use", () => {
    rememberRepo("/repos/abc/chat", null);
    expect(takeUpgradeMoment("0.57.0", true)).toBe("none");
  });
});
