import type { StatsLanguage } from "@repowise-dev/types/stats";
import { techName } from "../zoom/tech-names";
import { wholePct } from "./numbers";

/** Ink steps for the stacked bar, darkest first. Neutral: languages are not a status. */
const STEPS = [1, 0.55, 0.32, 0.18, 0.1, 0.06];

/**
 * Code languages by file count, as one stacked bar and a legend.
 *
 * Languages under 1% stay in the bar but leave the legend, so a repo with one
 * Dockerfile does not list it beside its main language.
 */
export function LanguageSplit({ languages }: { languages: StatsLanguage[] }) {
  const total = languages.reduce((a, l) => a + l.file_count, 0);
  if (total === 0) return null;
  const rows = languages.map((l, i) => ({
    name: techName(l.language),
    share: l.file_count / total,
    pct: wholePct(l.file_count, total),
    opacity: STEPS[i] ?? 0.05,
  }));

  return (
    <section aria-label="Languages" className="flex flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2 className="text-lg font-semibold text-[var(--color-text-primary)]">Languages</h2>
        <span className="font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
          By file count
        </span>
      </div>
      <div
        className="flex h-2.5"
        role="img"
        aria-label={rows.map((r) => `${r.name} ${r.pct}%`).join(", ")}
      >
        {rows.map((r) => (
          <div
            key={r.name}
            className="h-full bg-[var(--color-text-primary)]"
            style={{ width: `${r.share * 100}%`, opacity: r.opacity }}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-1.5 text-xs text-[var(--color-text-secondary)]">
        {rows
          .filter((r) => r.pct >= 1)
          .map((r) => (
            <span key={r.name} className="inline-flex items-center gap-1.5">
              <span
                className="inline-block h-2 w-2 bg-[var(--color-text-primary)]"
                style={{ opacity: r.opacity }}
                aria-hidden
              />
              {r.name} {r.pct}%
            </span>
          ))}
      </div>
    </section>
  );
}
