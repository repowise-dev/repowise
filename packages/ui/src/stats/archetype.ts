/**
 * Naming the shape of when work happens.
 *
 * A heatmap says *when*. A name says what kind of team this is, and that is the
 * part someone screenshots. The rule that keeps it from being a horoscope: every
 * name is earned by a stated threshold, and the reason travels with it so the
 * page can always show its working. When nothing is distinctive, the honest
 * neutral label wins rather than a forced joke.
 *
 * Naming lives here rather than server-side for one reason that is not
 * stylistic: half the thresholds depend on which days count as the weekend, and
 * that is a reader preference. A "weekend warrior" decided in Python would be
 * wrong for every team that rests Friday and Saturday.
 */

import type { StatsChronotype } from "@repowise-dev/types/stats";

export interface Archetype {
  /** Short display name. */
  name: string;
  /** The evidence that earned it. Rendered next to the name, always. */
  because: string;
}

/**
 * Below this, a label is noise rather than a habit.
 *
 * The chronotype list itself keeps the server's lower floor, because a peak
 * hour off 10 commits is still a fact. A *name* off 10 commits is a guess
 * wearing a costume, so naming holds out for more.
 */
export const NAME_MIN_COMMITS = 25;

/** Hours counted as the night block, and as the dawn block. */
const NIGHT_HOURS = [22, 23, 0, 1, 2, 3, 4];
const DAWN_HOURS = [5, 6, 7, 8];
/** Conventional working hours, 9am through 5:59pm. */
const CORE_HOURS = [9, 10, 11, 12, 13, 14, 15, 16, 17];

const sum = (ns: number[]): number => ns.reduce((a, b) => a + b, 0);
const pct = (part: number, total: number): number =>
  total > 0 ? Math.round((part / total) * 1000) / 10 : 0;

function shareOfHours(hours: number[], picks: number[], total: number): number {
  return pct(
    picks.reduce((a, h) => a + (hours[h] ?? 0), 0),
    total,
  );
}

/**
 * Fewest distinct hours-of-day that together hold 80% of the commits.
 *
 * A concentration measure that survives volume: someone with 600 commits inside
 * a six-hour window and someone with 30 inside the same window score the same.
 * High values mean the work never really stops.
 */
function hoursToCover80(hours: number[], total: number): number {
  if (total <= 0) return 0;
  const target = total * 0.8;
  let acc = 0;
  let n = 0;
  for (const v of [...hours].sort((a, b) => b - a)) {
    acc += v;
    n += 1;
    if (acc >= target) break;
  }
  return n;
}

function hourLabel(h: number): string {
  const period = h < 12 ? "am" : "pm";
  const twelve = h % 12 === 0 ? 12 : h % 12;
  return `${twelve}${period}`;
}

const DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

/**
 * Name a repository from its weekday-by-hour commit matrix.
 *
 * Tested in order, first match wins, so the most distinctive trait is the one
 * that gets to name you. Ordering is by how unusual the shape is, not by how
 * flattering it sounds: a repo that is both weekend-heavy and nocturnal is more
 * interestingly nocturnal.
 *
 * Returns null when there is not enough history to say anything.
 */
export function repoArchetype(
  matrix: number[][],
  weekendDays: readonly number[],
): Archetype | null {
  if (!matrix || matrix.length !== 7) return null;

  const dayTotals = matrix.map(sum);
  const total = sum(dayTotals);
  if (total < NAME_MIN_COMMITS) return null;

  const hours = Array.from({ length: 24 }, (_, h) => matrix.reduce((a, r) => a + (r[h] ?? 0), 0));
  const weekend = pct(
    dayTotals.reduce((a, v, wd) => a + (weekendDays.includes(wd) ? v : 0), 0),
    total,
  );
  const night = shareOfHours(hours, NIGHT_HOURS, total);
  const dawn = shareOfHours(hours, DAWN_HOURS, total);
  // The repo matrix carries the joint distribution, so "core" here is exact:
  // working hours on working days, not two marginals multiplied together.
  const core = pct(
    matrix.reduce(
      (a, row, wd) =>
        a + (weekendDays.includes(wd) ? 0 : CORE_HOURS.reduce((b, h) => b + (row[h] ?? 0), 0)),
      0,
    ),
    total,
  );
  const spread = hoursToCover80(hours, total);

  const busiestDay = DAY_NAMES[dayTotals.indexOf(Math.max(...dayTotals))] ?? "";
  const weekendNames = weekendDays
    .map((d) => DAY_NAMES[d]?.slice(0, 3))
    .filter(Boolean)
    .join(" and ");

  if (weekend >= 45) {
    return {
      name: "Weekend Project",
      because: `${weekend}% of commits land on ${weekendNames}. This is somebody's nights-and-weekends thing.`,
    };
  }
  if (night >= 20) {
    return {
      name: "Night Shift",
      because: `${night}% of commits are written between 10pm and 5am.`,
    };
  }
  if (dawn >= 20) {
    return {
      name: "Dawn Patrol",
      because: `${dawn}% of commits land before 9am, most of it shipped before the day starts.`,
    };
  }
  if (weekend >= 28) {
    return {
      name: "Weekend Workshop",
      because: `${weekend}% of commits land on ${weekendNames}. The week does not stop on Friday.`,
    };
  }
  if (core >= 65 && weekend < 15) {
    return {
      name: "Office Hours",
      because: `${core}% of commits fall inside working hours on working days.`,
    };
  }
  if (spread >= 14) {
    return {
      name: "Always On",
      because: `It takes ${spread} different hours of the day to account for 80% of the work.`,
    };
  }
  return {
    name: "Steady State",
    because: `Busiest on ${busiestDay}s, with no strong pull toward any part of the clock.`,
  };
}

/**
 * Name a single contributor from their two marginal histograms.
 *
 * Only the marginals are available here, so "nine to fiver" is a conjunction of
 * two separate facts (mostly core hours, almost never at the weekend) rather
 * than a true joint measurement. That is a deliberate trade: the exact version
 * costs 168 numbers per person over the wire to sharpen one word.
 *
 * Returns null below the naming floor, or on a payload predating the arrays.
 */
export function contributorArchetype(
  person: StatsChronotype,
  weekendDays: readonly number[],
): Archetype | null {
  const hours = person.hour_commits;
  const weekdays = person.weekday_commits;
  if (!hours || hours.length !== 24 || !weekdays || weekdays.length !== 7) return null;

  const total = sum(hours);
  if (total < NAME_MIN_COMMITS) return null;

  const weekend = pct(
    weekdays.reduce((a, v, wd) => a + (weekendDays.includes(wd) ? v : 0), 0),
    total,
  );
  const night = shareOfHours(hours, NIGHT_HOURS, total);
  const dawn = shareOfHours(hours, DAWN_HOURS, total);
  const core = shareOfHours(hours, CORE_HOURS, total);
  const spread = hoursToCover80(hours, total);
  const peak = person.peak_hour;

  if (night >= 25 || peak >= 22 || peak <= 4) {
    return { name: "Night Owl", because: `${night}% of their commits land between 10pm and 5am` };
  }
  if (dawn >= 25 || (peak >= 5 && peak <= 8)) {
    return { name: "Dawn Patrol", because: `${dawn}% of their commits land before 9am` };
  }
  if (weekend >= 33) {
    return { name: "Weekend Warrior", because: `${weekend}% of their commits land at the weekend` };
  }
  if (core >= 70 && weekend < 15) {
    return {
      name: "Nine to Fiver",
      because: `${core}% between 9am and 6pm, and almost never at the weekend`,
    };
  }
  if (spread <= 6) {
    return {
      name: "Clockwork",
      because: `80% of their work fits inside ${spread} hours of the day`,
    };
  }
  if (peak >= 17 && peak <= 21) {
    return { name: "The Closer", because: `peaks at ${hourLabel(peak)}, after most people stop` };
  }
  if (peak >= 12 && peak <= 16) {
    return { name: "Afternoon Regular", because: `peaks reliably around ${hourLabel(peak)}` };
  }
  return { name: "Daylight", because: `peaks around ${hourLabel(peak)}, no strong off-hours pull` };
}
