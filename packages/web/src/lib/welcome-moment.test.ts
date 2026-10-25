// @vitest-environment jsdom

import { beforeEach, describe, expect, it } from "vitest";
import type { OverviewSummaryResponse } from "@repowise-dev/types/overview";
import {
  decideMoment,
  isNewBrowser,
  markVersionSeen,
  sessionMomentTaken,
  takeSessionMoment,
  takeUpgradeMoment,
} from "./release-moment";
import {
  decideWelcome,
  historyYears,
  welcomeFigures,
  welcomeLenses,
  type WelcomeExtras,
  type WelcomeFigures,
  type WelcomeOpenInput,
} from "./welcome-moment";

beforeEach(() => {
  window.localStorage.clear();
  window.sessionStorage.clear();
});

const NEW_USER: WelcomeOpenInput = {
  search: "",
  fileCount: 1284,
  seen: false,
  newBrowser: true,
  sessionTaken: false,
};

describe("decideWelcome", () => {
  it("opens once for a new browser on an indexed, unseen repo", () => {
    expect(decideWelcome(NEW_USER)).toBe("auto");
  });

  it("never opens by itself for an existing user", () => {
    expect(decideWelcome({ ...NEW_USER, newBrowser: false })).toBe("none");
  });

  it("stays shut once seen, before the seen mark is known, and before a first index", () => {
    expect(decideWelcome({ ...NEW_USER, seen: true })).toBe("none");
    expect(decideWelcome({ ...NEW_USER, seen: null })).toBe("none");
    expect(decideWelcome({ ...NEW_USER, fileCount: 0 })).toBe("none");
  });

  it("waits for the next session when What's new already showed", () => {
    expect(decideWelcome({ ...NEW_USER, sessionTaken: true })).toBe("none");
  });

  it("opens on demand with ?tour=1 past every guard", () => {
    expect(
      decideWelcome({ search: "?tour=1", fileCount: 0, seen: true, newBrowser: false, sessionTaken: true }),
    ).toBe("tour");
    expect(decideWelcome({ ...NEW_USER, newBrowser: false, search: "?tour=0" })).toBe("none");
  });
});

describe("new browser and the session guard", () => {
  it("a first visit counts as new, and stays new after the release moment stores a version", () => {
    expect(isNewBrowser()).toBe(true);
    takeUpgradeMoment("0.59.0", true);
    expect(isNewBrowser()).toBe(true);
  });

  it("stays new when the welcome stores the version before the release moment runs", () => {
    markVersionSeen("0.59.0");
    expect(isNewBrowser()).toBe(true);
  });

  it("a browser with a stored version or earlier repowise state is an existing user", () => {
    window.localStorage.setItem("repowise:last-seen-server-version", "0.58.0");
    expect(isNewBrowser()).toBe(false);
    window.localStorage.clear();
    window.localStorage.setItem("repowise:locale", "en");
    expect(isNewBrowser()).toBe(false);
    takeUpgradeMoment("0.59.0", true);
    expect(isNewBrowser()).toBe(false);
  });

  it("What's new takes the session, so the welcome waits", () => {
    window.localStorage.setItem("repowise:last-seen-server-version", "0.58.0");
    expect(decideMoment("0.59.0", [], "").moment).toBe("dialog");
    expect(sessionMomentTaken()).toBe(true);
    expect(decideWelcome({ ...NEW_USER, sessionTaken: sessionMomentTaken() })).toBe("none");
  });

  it("the welcome takes the session, so the release moment waits without using up the upgrade", () => {
    window.localStorage.setItem("repowise:last-seen-server-version", "0.58.0");
    takeSessionMoment();
    expect(decideMoment("0.59.0", [], "")).toEqual({ moment: "none", preview: false });
    expect(window.localStorage.getItem("repowise:last-seen-server-version")).toBe("0.58.0");
    // A replay still works.
    expect(decideMoment("0.59.0", [], "?whats-new=preview").moment).toBe("dialog");
  });
});

const FIGURES: WelcomeFigures = {
  health: { score: 7.4, band: "good", worstPath: "a.py", worstScore: 3.1 },
  decisionsToReview: 14,
  docPages: 412,
  coveragePct: null,
};
const EXTRAS: WelcomeExtras = {
  deadCode: { files: 38, safe: 12 },
  risk: { path: "auth/session.py", reached: 214 },
  testsReached: { reached: 61, total: 100 },
};

describe("welcomeLenses", () => {
  it("shows all six lenses in order when everything was measured", () => {
    expect(welcomeLenses(FIGURES, EXTRAS).map((l) => l.key)).toEqual([
      "health",
      "deadCode",
      "risk",
      "decisions",
      "docs",
      "testsInferred",
    ]);
  });

  it("drops every lens whose value was not measured", () => {
    const none = welcomeLenses(
      { health: null, decisionsToReview: null, docPages: null, coveragePct: null },
      { deadCode: null, risk: null, testsReached: null },
    );
    expect(none).toEqual([]);
    const partial = welcomeLenses(
      { ...FIGURES, docPages: 0 },
      { ...EXTRAS, risk: { path: "x.py", reached: 0 }, testsReached: { reached: 0, total: 0 } },
    );
    expect(partial.map((l) => l.key)).toEqual(["health", "deadCode", "decisions"]);
  });

  it("prefers measured coverage over the inferred map", () => {
    const lenses = welcomeLenses({ ...FIGURES, coveragePct: 72.4 }, EXTRAS);
    expect(lenses.at(-1)).toEqual({ key: "tests", measuredPct: 72.4 });
  });
});

describe("welcomeFigures", () => {
  it("leaves out what the payload does not carry", () => {
    const summary = {
      health: { average_health: null, worst_performer_path: null, worst_performer_score: null },
      stats: {},
    } as unknown as OverviewSummaryResponse;
    expect(welcomeFigures(summary)).toEqual({
      health: null,
      decisionsToReview: null,
      docPages: null,
      coveragePct: null,
    });
  });
});

describe("historyYears", () => {
  it("counts whole years and drops anything under one", () => {
    expect(historyYears(9 * 365 + 20)).toBe(9);
    expect(historyYears(200)).toBeNull();
    expect(historyYears(null)).toBeNull();
  });
});
