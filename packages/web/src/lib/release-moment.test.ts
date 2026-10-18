// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from "vitest";
import {
  NEW_MARK_DAYS,
  activeNewRoutes,
  decideMoment,
  highlightRepoId,
  rememberRepo,
  linkPullRequests,
  previewMoment,
  readNewSurfaces,
  repoIdFromPath,
  resolveHighlightHref,
  routeMatches,
  startNewSurfaces,
  takeUpgradeMoment,
  upgradeMoment,
  visitNewSurface,
} from "./release-moment";

const HL = (href: string | null) => ({ id: "x", title: "X", blurb: "", href });

beforeEach(() => window.localStorage.clear());

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
