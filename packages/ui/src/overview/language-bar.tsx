import * as React from "react";

/**
 * Language mix as a stacked bar plus a key.
 *
 * Replaces a donut that spent roughly 200px of height rendering four numbers
 * you can read faster as text, and did it in four unrelated hues that fought
 * every other colour on the page. The bar is one accent stepped down in
 * lightness, so proportion still reads at a glance without introducing a
 * second palette.
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
  const segments: { name: string; count: number; color: string }[] = shown.map(
    ([name, count], i) => ({
      name,
      count,
      // Step the accent down toward the inset surface rather than reaching for
      // new hues: proportion is the message, identity of each language is the
      // key's job.
      color:
        i === 0
          ? "var(--color-accent-fill)"
          : `color-mix(in srgb, var(--color-accent-fill) ${Math.max(12, 70 - i * 16)}%, var(--color-bg-inset))`,
    }),
  );
  if (restCount > 0) {
    segments.push({ name: "Other", count: restCount, color: "var(--color-bg-inset)" });
  }

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
        {segments.map((s) => (
          <span
            key={s.name}
            title={`${s.name}: ${s.count.toLocaleString()} files`}
            className="h-full"
            style={{ width: `${(s.count / total) * 100}%`, background: s.color }}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-[10px] text-[var(--color-text-tertiary)]">
        {segments.map((s) => (
          <span key={s.name} className="inline-flex items-center">
            <span
              aria-hidden
              className="mr-1.5 inline-block h-1.5 w-1.5 rounded-sm"
              style={{ background: s.color }}
            />
            {s.name}{" "}
            <span className="ml-1 tabular-nums text-[var(--color-text-secondary)]">
              {Math.round((s.count / total) * 100)}%
            </span>
          </span>
        ))}
      </div>
    </div>
  );
}
