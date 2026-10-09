import * as React from "react";

/**
 * Two hues then neutrals, in the order the rule allows. The accent leads
 * because the first segment is the repository's primary language; plum is the
 * pairing `globals.css` already sanctions beside it; the rest recede, which is
 * also the honest encoding, since a 2% slice is not as interesting as a 60%
 * one.
 */
const SEGMENTS: { bg: string; ink: string }[] = [
  // `ink` is a fixed value per fill rather than a theme token. Both fills hold
  // their own lightness in either theme — the orange is light, the plum is
  // dark — so a token that flips with the theme is guaranteed to be wrong on
  // one of them half the time.
  { bg: "var(--color-accent-fill)", ink: "#1a1205" },
  { bg: "var(--color-accent-secondary)", ink: "#f5f1ea" },
  { bg: "var(--color-neutral-1)", ink: "var(--color-text-secondary)" },
  { bg: "var(--color-neutral-2)", ink: "var(--color-text-secondary)" },
  { bg: "var(--color-neutral-3)", ink: "var(--color-text-secondary)" },
];

const OTHER_SEGMENT = {
  bg: "var(--color-bg-inset)",
  ink: "var(--color-text-secondary)",
};

/** Below this share a segment is too narrow to hold its own name legibly. */
const INLINE_LABEL_MIN_PCT = 12;

/**
 * Language mix as a stacked bar plus a key.
 *
 * Replaces a donut that spent roughly 200px of height rendering four numbers
 * you can read faster as text, and did it in four unrelated hues that fought
 * every other colour on the page.
 *
 * Two problems with what replaced it, both fixed here.
 *
 * It painted the segments with `--color-ramp-*`, whose own definition says
 * "ramp means magnitude; NOT for unrelated categories, which want either the
 * accent/secondary pair or plain neutrals". Languages are unrelated
 * categories, and five steps of one hue in a 6px bar are not separable — you
 * cannot match a swatch in the key back to a segment. It now uses the pairing
 * the rule sanctions: the accent, the plum secondary, then neutrals.
 *
 * More importantly, the widest segments carry their own name. That removes the
 * matching problem outright rather than making it slightly easier, and the key
 * is left to do the job only the small segments still need.
 */
export function LanguageBar({
  distribution,
  maxShown = 5,
}: {
  /** language → file count. */
  distribution: Record<string, number>;
  maxShown?: number;
}) {
  const entries = Object.entries(distribution)
    .filter(([, n]) => n > 0)
    .sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((s, [, n]) => s + n, 0);
  if (total === 0) return null;

  const shown = entries.slice(0, maxShown);
  const restCount = entries.slice(maxShown).reduce((s, [, n]) => s + n, 0);
  const segments = shown.map(([name, count], i) => ({
    name,
    count,
    ...(SEGMENTS[i] ?? OTHER_SEGMENT),
    pct: (count / total) * 100,
  }));
  if (restCount > 0) {
    segments.push({
      name: "Other",
      count: restCount,
      ...OTHER_SEGMENT,
      pct: (restCount / total) * 100,
    });
  }

  return (
    <div className="flex flex-col gap-2.5">
      {/* Tall enough to hold type. The 6px bar it replaces could only ever be
          decoded through the key. */}
      <div className="flex h-6 w-full overflow-hidden rounded-md bg-[var(--color-bg-inset)]">
        {segments.map((s) => (
          <span
            key={s.name}
            title={`${s.name}: ${s.count.toLocaleString()} files`}
            className="flex h-full min-w-0 items-center justify-center overflow-hidden px-1.5"
            style={{ width: `${s.pct}%`, background: s.bg }}
          >
            {s.pct >= INLINE_LABEL_MIN_PCT && (
              <span
                className="truncate font-mono text-[10px] font-medium"
                style={{ color: s.ink }}
              >
                {s.name} {Math.round(s.pct)}%
              </span>
            )}
          </span>
        ))}
      </div>
      {/* Only what the bar could not label itself. A key that repeats every
          segment makes the inline labels redundant rather than helpful. */}
      <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px] text-[var(--color-text-tertiary)]">
        {segments
          .filter((s) => s.pct < INLINE_LABEL_MIN_PCT)
          .map((s) => (
            <span key={s.name} className="inline-flex items-center">
              <span
                aria-hidden
                className="mr-1.5 inline-block h-1.5 w-1.5 rounded-sm"
                style={{ background: s.bg }}
              />
              {s.name}{" "}
              <span className="ml-1 tabular-nums text-[var(--color-text-secondary)]">
                {Math.round(s.pct)}%
              </span>
            </span>
          ))}
      </div>
    </div>
  );
}
