import { render, screen } from "@testing-library/react";
import type { StatsPunchCard } from "@repowise-dev/types/stats";
import { describe, expect, it } from "vitest";
import { PunchCard } from "../../src/stats/punch-card.js";
import { weekendDaysFor, weekendShare } from "../../src/stats/weekend.js";

/** 10 commits: 6 on Wed, 2 on Fri, 1 on Sat, 1 on Sun. */
function makePunchCard(): StatsPunchCard {
  const matrix = Array.from({ length: 7 }, () => Array<number>(24).fill(0));
  matrix[2]![14] = 6;
  matrix[4]![10] = 2;
  matrix[5]![11] = 1;
  matrix[6]![12] = 1;
  return {
    matrix,
    peak: { weekday: 2, hour: 14, count: 6 },
    busiest_weekday: 2,
    peak_hour: 14,
    timezone_mode: "author_local" as const,
  total: 10,
  };
}

describe("weekendShare", () => {
  it("counts only the configured weekend days", () => {
    const { matrix } = makePunchCard();

    expect(weekendShare(matrix, weekendDaysFor("sat-sun"))).toBe(20);
    expect(weekendShare(matrix, weekendDaysFor("fri-sat"))).toBe(30);
    expect(weekendShare(matrix, weekendDaysFor("fri"))).toBe(20);
  });

  it("falls back to Sat/Sun for an unknown or missing preset", () => {
    const { matrix } = makePunchCard();

    expect(weekendShare(matrix, weekendDaysFor(undefined))).toBe(20);
    expect(weekendShare(matrix, weekendDaysFor("nonsense"))).toBe(20);
  });

  it("reports zero rather than dividing by zero on an empty matrix", () => {
    const empty = Array.from({ length: 7 }, () => Array<number>(24).fill(0));
    expect(weekendShare(empty, [5, 6])).toBe(0);
  });
});

describe("PunchCard", () => {
  it("defaults the weekend share to Sat/Sun", () => {
    render(<PunchCard data={makePunchCard()} />);
    expect(screen.getByText("20%")).toBeInTheDocument();
  });

  it("honours a Friday/Saturday weekend", () => {
    render(<PunchCard data={makePunchCard()} weekendDays={weekendDaysFor("fri-sat")} />);
    expect(screen.getByText("30%")).toBeInTheDocument();
  });

  it("puts the total in the header, so the chart's scale is never in doubt", () => {
    render(<PunchCard data={makePunchCard()} />);
    expect(screen.getByText("10")).toBeInTheDocument();
  });

  it("names the hottest single slot, which the lattice alone cannot show", () => {
    render(<PunchCard data={makePunchCard()} />);
    expect(screen.getByText("Hottest slot")).toBeInTheDocument();
    expect(screen.getByText(/Wed 2 PM/)).toBeInTheDocument();
  });

  it("reports the stretch nobody has ever committed in", () => {
    render(<PunchCard data={makePunchCard()} />);
    // The fixture only ever commits at 10, 11, 12 and 14, so the longest silent
    // run wraps midnight: 15:00 through 09:59.
    expect(screen.getByText("Quietest stretch")).toBeInTheDocument();
    expect(screen.getByText(/3 PM/)).toBeInTheDocument();
  });

  it("withholds a name below the naming floor rather than guess", () => {
    render(<PunchCard data={makePunchCard()} />);
    expect(screen.queryByText("Ships like a")).not.toBeInTheDocument();
  });

  it("names the repo once there is enough history to mean it", () => {
    const data = makePunchCard();
    // 40 commits, all on Saturday afternoon: unambiguously weekend-led.
    data.matrix[5]![14] = 40;
    data.total = 50;
    data.peak = { weekday: 5, hour: 14, count: 40 };
    render(<PunchCard data={data} />);
    expect(screen.getByText("Ships like a")).toBeInTheDocument();
    expect(screen.getByText("Weekend Project")).toBeInTheDocument();
  });
});
