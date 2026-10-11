/**
 * The naming rules, held to their own thresholds.
 *
 * The failure mode worth guarding is a name that reads as personality but is
 * really an artifact: a label handed out below the evidence floor, a "weekend
 * warrior" that ignores the reader's weekend preset, or a nocturnal repo losing
 * its most distinctive trait to a blander rule that happened to match first.
 */

import { describe, expect, it } from "vitest";
import type { StatsChronotype } from "@repowise-dev/types/stats";
import {
  contributorArchetype,
  NAME_MIN_COMMITS,
  repoArchetype,
} from "../../src/stats/archetype.js";
import { weekendDaysFor } from "../../src/stats/weekend.js";

const SAT_SUN = weekendDaysFor("sat-sun");
const FRI_SAT = weekendDaysFor("fri-sat");

/** Empty 7x24 matrix, 0 = Monday. */
function matrix(): number[][] {
  return Array.from({ length: 7 }, () => Array<number>(24).fill(0));
}

/** Put `n` commits on `weekday` at `hour`. */
function at(m: number[][], weekday: number, hour: number, n: number): number[][] {
  m[weekday]![hour] = (m[weekday]![hour] ?? 0) + n;
  return m;
}

function person(over: Partial<StatsChronotype> = {}): StatsChronotype {
  return {
    name: "Someone",
    commits: 100,
    peak_hour: 14,
    label: "daylight",
    night_pct: 0,
    early_pct: 0,
    hour_commits: Array<number>(24).fill(0),
    weekday_commits: Array<number>(7).fill(0),
    ...over,
  };
}

describe("repoArchetype", () => {
  it("stays silent below the evidence floor", () => {
    const m = at(matrix(), 5, 14, NAME_MIN_COMMITS - 1);
    expect(repoArchetype(m, SAT_SUN)).toBeNull();
  });

  it("names a weekend-led repo", () => {
    const m = at(at(matrix(), 5, 14, 60), 2, 14, 40);
    expect(repoArchetype(m, SAT_SUN)?.name).toBe("Weekend Project");
  });

  it("reads the weekend from the reader's preset, not from Sat/Sun", () => {
    // All the work is on Friday, which is a weekend day in some of the world
    // and an ordinary working day in the rest of it.
    const m = at(matrix(), 4, 14, 80);
    expect(repoArchetype(m, FRI_SAT)?.name).toBe("Weekend Project");
    expect(repoArchetype(m, SAT_SUN)?.name).not.toBe("Weekend Project");
  });

  it("prefers the rarer trait when a repo is both nocturnal and weekend-heavy", () => {
    // 35% weekend would earn "Weekend Workshop", but a third of the work
    // landing after 10pm is the more distinctive fact about this repo.
    const m = matrix();
    at(m, 5, 23, 35);
    at(m, 2, 14, 65);
    const out = repoArchetype(m, SAT_SUN);
    expect(out?.name).toBe("Night Shift");
    expect(out?.because).toContain("10pm");
  });

  it("names a conventional weekday repo from working hours", () => {
    const m = matrix();
    [0, 1, 2, 3, 4].forEach((wd) => [10, 11, 14, 15].forEach((h) => at(m, wd, h, 10)));
    expect(repoArchetype(m, SAT_SUN)?.name).toBe("Office Hours");
  });

  it("calls out a repo whose work never really stops", () => {
    const m = matrix();
    // Every waking hour, evenly, on weekdays. Deliberately not a full 24-hour
    // spread: that would put 7 of 24 hours in the night block and earn "Night
    // Shift" instead, which would be the correct reading of it.
    Array.from({ length: 18 }, (_, i) => i + 6).forEach((h) => {
      [0, 1, 2, 3, 4].forEach((wd) => at(m, wd, h, 3));
    });
    expect(repoArchetype(m, SAT_SUN)?.name).toBe("Always On");
  });

  it("says so plainly when no shape is distinctive", () => {
    const m = matrix();
    [0, 1, 2, 3, 4].forEach((wd) => [10, 14, 19, 20].forEach((h) => at(m, wd, h, 4)));
    at(m, 5, 14, 15);
    const out = repoArchetype(m, SAT_SUN);
    expect(out?.name).toBe("Steady State");
  });

  it("refuses a malformed matrix rather than naming from it", () => {
    expect(repoArchetype([], SAT_SUN)).toBeNull();
    expect(repoArchetype([[1, 2]], SAT_SUN)).toBeNull();
  });
});

describe("contributorArchetype", () => {
  it("stays silent below the naming floor", () => {
    const hours = Array<number>(24).fill(0);
    hours[23] = NAME_MIN_COMMITS - 1;
    const weekdays = Array<number>(7).fill(0);
    weekdays[2] = NAME_MIN_COMMITS - 1;
    expect(contributorArchetype(person({ hour_commits: hours, weekday_commits: weekdays }), SAT_SUN)).toBeNull();
  });

  it("stays silent on a payload predating the histograms", () => {
    const legacy = person();
    // @ts-expect-error deliberately modelling an older server response
    delete legacy.hour_commits;
    expect(contributorArchetype(legacy, SAT_SUN)).toBeNull();
  });

  it("names a night owl", () => {
    const hours = Array<number>(24).fill(0);
    hours[23] = 40;
    hours[14] = 60;
    const weekdays = Array<number>(7).fill(0);
    weekdays[2] = 100;
    const out = contributorArchetype(
      person({ hour_commits: hours, weekday_commits: weekdays, peak_hour: 14 }),
      SAT_SUN,
    );
    expect(out?.name).toBe("Night Owl");
  });

  it("names a weekend warrior against the reader's own weekend", () => {
    const hours = Array<number>(24).fill(0);
    hours[14] = 100;
    const weekdays = Array<number>(7).fill(0);
    weekdays[4] = 50;
    weekdays[2] = 50;
    const p = person({ hour_commits: hours, weekday_commits: weekdays });
    expect(contributorArchetype(p, FRI_SAT)?.name).toBe("Weekend Warrior");
    expect(contributorArchetype(p, SAT_SUN)?.name).not.toBe("Weekend Warrior");
  });

  it("separates a nine-to-fiver from someone merely concentrated", () => {
    const weekdays = Array<number>(7).fill(0);
    weekdays[1] = 100;

    const office = Array<number>(24).fill(0);
    [9, 10, 11, 13, 14, 15, 16, 17].forEach((h) => (office[h] = 12));
    office[12] = 4;
    expect(
      contributorArchetype(person({ hour_commits: office, weekday_commits: weekdays }), SAT_SUN)
        ?.name,
    ).toBe("Nine to Fiver");

    // Same tightness, but centred on the evening, so it is not office hours.
    const evening = Array<number>(24).fill(0);
    [18, 19, 20].forEach((h) => (evening[h] = 33));
    evening[21] = 1;
    expect(
      contributorArchetype(
        person({ hour_commits: evening, weekday_commits: weekdays, peak_hour: 19 }),
        SAT_SUN,
      )?.name,
    ).toBe("Clockwork");
  });

  it("carries its evidence so the label can always show its working", () => {
    const hours = Array<number>(24).fill(0);
    hours[23] = 100;
    const weekdays = Array<number>(7).fill(0);
    weekdays[2] = 100;
    const out = contributorArchetype(
      person({ hour_commits: hours, weekday_commits: weekdays, peak_hour: 23 }),
      SAT_SUN,
    );
    expect(out?.because).toMatch(/100%/);
  });
});
