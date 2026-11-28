import { fireEvent, render, screen } from "@testing-library/react";
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

  it("draws both marginal totals, so magnitudes never need a hover", () => {
    render(<PunchCard data={makePunchCard()} />);
    // Wednesday's total sits beside its row.
    expect(screen.getAllByText("6").length).toBeGreaterThan(0);
  });

  it("opens on the hottest single slot, which the lattice alone cannot show", () => {
    render(<PunchCard data={makePunchCard()} />);
    expect(screen.getByText(/Busiest hour/)).toHaveTextContent("Busiest hour: Wednesday, 2pm, 6 commits.");
  });

  it("reports the stretch nobody has ever committed in, on enough history", () => {
    const data = makePunchCard();
    data.matrix[2]![14] = 196;
    data.total = 200;
    render(<PunchCard data={data} />);
    // Commits only ever land at 10, 11, 12 and 14, so the longest silent run
    // wraps midnight: 15:00 through 09:59.
    expect(screen.getByText("Quiet hours")).toBeInTheDocument();
    expect(screen.getByText("3pm")).toBeInTheDocument();
    expect(screen.getByText(/No commit has ever landed/)).toBeInTheDocument();
  });

  it("keeps quiet hours off on thin history", () => {
    render(<PunchCard data={makePunchCard()} />);
    expect(screen.queryByText("Quiet hours")).not.toBeInTheDocument();
  });

  it("does not say ever when the matrix is a partial window", () => {
    const data = makePunchCard();
    data.matrix[2]![14] = 196;
    data.total = 200;
    render(<PunchCard data={data} partialCommits={2000} />);
    expect(screen.getByText(/None of the latest 2,000 commits landed/)).toBeInTheDocument();
    expect(screen.queryByText(/ever/)).not.toBeInTheDocument();
  });

  it("ignores weekend indexes outside the week", () => {
    render(<PunchCard data={makePunchCard()} weekendDays={[5, 6, 9, -1]} />);
    expect(screen.getByText("20%")).toBeInTheDocument();
    expect(screen.getByText(/land on Saturday and Sunday\./)).toBeInTheDocument();
  });

  it("drops the pinned hour when focus leaves the grid", () => {
    const { container } = render(<PunchCard data={makePunchCard()} />);
    const grid = screen.getByRole("group");
    fireEvent.keyDown(grid, { key: "ArrowRight" });
    expect(screen.getByText(/Wednesday, 3pm/)).toBeInTheDocument();
    fireEvent.blur(grid, { relatedTarget: null });
    expect(container.textContent).toContain("Busiest hour");
  });

  it("says which clock the hours are in, in visible text", () => {
    render(<PunchCard data={{ ...makePunchCard(), timezone_mode: "utc" }} />);
    expect(screen.getByText("Hours in UTC")).toBeInTheDocument();
    expect(screen.getByText(/Too few commits carry a UTC offset/)).toBeInTheDocument();
  });
});
