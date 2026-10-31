import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";

import {
  ContractTypeCounts,
  WorkspaceOverviewSections,
  lowestHealthStat,
  lowestRepoHealth,
} from "../../src/workspace/workspace-overview.js";
import { healthMarkTextColor } from "../../src/health/tokens.js";

const copy = {
  label: "Lowest health",
  sub: (low: { name: string; healthy: number; scored: number }) =>
    `${low.name} · ${low.healthy} of ${low.scored} healthy`,
  none: "no health snapshot yet",
};

describe("lowestRepoHealth", () => {
  it("names the weakest scored repository and counts the healthy ones", () => {
    const low = lowestRepoHealth([
      { name: "frontend", health: 8.0 },
      { name: "backend", health: 7.7 },
      { name: "docs", health: null },
      { name: "repowise", health: 8.1 },
    ]);
    expect(low).toMatchObject({ name: "backend", score: 7.7, healthy: 2, scored: 3 });
  });

  it("keeps a measured zero as the lowest, and reads no scores as none", () => {
    expect(lowestRepoHealth([{ name: "a", health: 0 }, { name: "b", health: 5 }])?.name).toBe("a");
    expect(lowestRepoHealth([{ name: "a", health: null }])).toBeNull();
    expect(lowestRepoHealth([])).toBeNull();
  });
});

describe("lowestHealthStat", () => {
  it("takes the colour the repository's own row takes", () => {
    const stat = lowestHealthStat(lowestRepoHealth([{ name: "backend", health: 7.7 }]), copy);
    expect(stat).toMatchObject({
      label: "Lowest health",
      value: "7.7",
      valueColor: healthMarkTextColor(7.7),
      sub: "backend · 0 of 1 healthy",
    });
  });

  it("shows a dash, never a zero, when nothing is scored", () => {
    const stat = lowestHealthStat(null, copy);
    expect(stat.value).toBe("—");
    expect(stat.valueColor).toBeUndefined();
    expect(stat.sub).toBe("no health snapshot yet");
  });
});

describe("WorkspaceOverviewSections", () => {
  it("puts the repositories before Do next, and Do next before contracts and co-changes", () => {
    const { container } = render(
      <WorkspaceOverviewSections
        coChanges={<p>co-changes</p>}
        contracts={<p>contracts</p>}
        nextActions={<p>do next</p>}
        repositories={<p>repositories</p>}
        ribbon={<p>ribbon</p>}
        lede={<p>lede</p>}
      />,
    );
    expect([...container.querySelectorAll("p")].map((p) => p.textContent)).toEqual([
      "lede",
      "ribbon",
      "repositories",
      "do next",
      "contracts",
      "co-changes",
    ]);
  });
});

describe("ContractTypeCounts", () => {
  it("lists contract types largest first", () => {
    const { container } = render(<ContractTypeCounts byType={{ table: 425, http: 953, code: 1272 }} />);
    expect([...container.querySelectorAll("dd")].map((d) => d.textContent)).toEqual([
      "1,272",
      "953",
      "425",
    ]);
  });
});
