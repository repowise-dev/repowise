import { describe, expect, it } from "vitest";

import { normalizeRepoPath, repoLabel } from "../src/repos.js";

describe("repoLabel", () => {
  it("shows the workspace's display name when one is set", () => {
    expect(repoLabel({ display_name: "Billing API" }, "svc-02")).toBe("Billing API");
  });

  it("falls back to the name the surface showed before", () => {
    expect(repoLabel({}, "svc-02")).toBe("svc-02");
    expect(repoLabel({ display_name: null }, "svc-02")).toBe("svc-02");
    expect(repoLabel({ display_name: "  " }, "svc-02")).toBe("svc-02");
  });
});

describe("normalizeRepoPath on Windows", () => {
  it("turns drive paths into lowercase forward-slash form", () => {
    expect(normalizeRepoPath("C:\\Users\\Dev\\Repo", true)).toBe("c:/users/dev/repo");
  });

  it("treats both separators alike", () => {
    expect(normalizeRepoPath("C:\\Users/Dev\\Repo/src", true)).toBe("c:/users/dev/repo/src");
    expect(normalizeRepoPath("C:/Users/Dev/Repo", true)).toBe(
      normalizeRepoPath("c:\\users\\dev\\repo", true),
    );
  });

  it("drops trailing separators of either kind", () => {
    expect(normalizeRepoPath("C:\\Repo\\", true)).toBe("c:/repo");
    expect(normalizeRepoPath("C:\\Repo\\/\\", true)).toBe("c:/repo");
    expect(normalizeRepoPath("C:\\", true)).toBe("c:");
  });

  it("handles UNC paths", () => {
    expect(normalizeRepoPath("\\\\Server\\Share\\Repo", true)).toBe("//server/share/repo");
  });
});

describe("normalizeRepoPath on POSIX", () => {
  it("keeps case", () => {
    expect(normalizeRepoPath("/Users/Dev/Repo", false)).toBe("/Users/Dev/Repo");
  });

  it("drops trailing slashes only", () => {
    expect(normalizeRepoPath("/home/dev/repo//", false)).toBe("/home/dev/repo");
    expect(normalizeRepoPath("/", false)).toBe("");
  });

  it("keeps backslashes, which are filename characters there", () => {
    expect(normalizeRepoPath("/tmp/a\\b", false)).toBe("/tmp/a\\b");
  });
});
