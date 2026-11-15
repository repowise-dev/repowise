import { afterEach, describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { HealthPageChrome, type HealthPageChromeProps } from "../../src/health/page-chrome.js";

afterEach(() => {
  vi.useRealTimers();
});

function metaLine(meta: Exclude<HealthPageChromeProps["meta"], undefined>) {
  const { container } = render(
    <HealthPageChrome repoId="r1" active="overview" title="Code health" meta={meta} />,
  );
  return container.querySelector("p")?.textContent ?? null;
}

describe("HealthPageChrome index line", () => {
  it("reads the index age, short commit and snapshot count", () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-03-22T12:00:00Z"));
    expect(
      metaLine({
        last_indexed_at: "2026-03-20T12:00:00Z",
        head_commit: "abcdef1234567",
        snapshot_count: 3,
      }),
    ).toBe("Indexed 2d ago · abcdef1 · 3 snapshots");
  });

  it("says never for a repo not indexed yet, and drops empty parts", () => {
    expect(metaLine({ last_indexed_at: null, head_commit: null, snapshot_count: 0 })).toBe(
      "Indexed never",
    );
  });

  it("renders no index line without meta", () => {
    expect(metaLine(null)).toBeNull();
  });
});
