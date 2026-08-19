import * as React from "react";

export interface ReadBarSegment {
  /** Share of the whole, 0–1. */
  fraction: number;
  /** CSS colour. Keep to accent tints so the bar stays inside the one-accent
   *  rule; semantic red/amber/green belong to health, not to proportions. */
  color: string;
  /** Tooltip text for this segment. */
  title: string;
}

export interface ReadItem {
  key: string;
  label: string;
  value: string;
  /** Trailing unit or secondary figure, rendered small and quiet. */
  unit?: string;
  /** One line saying what the number means. Not optional in spirit: a figure
   *  with no frame is the thing this whole redesign is fixing. */
  why: string;
  href: string;
  /** Optional proportion bar — use only when the number genuinely splits. */
  bar?: ReadBarSegment[];
}

/**
 * The other reasons people open Overview, beside the health hero.
 *
 * Code health is the product's moat and keeps the biggest number, but it
 * cannot be the only reason to visit: someone here for documentation
 * coverage, for what their agent has been costing, or for index freshness
 * should find their figure at the top of the page rather than three scrolls
 * down or buried in a footer. Same altitude, smaller type.
 *
 * Every row is a link, so this column doubles as navigation for the people who
 * did not come for health.
 *
 * Portability: the caller supplies the list. Hosted has no local `.repowise/`
 * directory and therefore no index-storage figure, so it composes the same
 * component with that row simply absent — no flag, no dead branch. Same for
 * agent savings, which come from a CLI-written sidecar.
 */
export function ReadsColumn({
  items,
  LinkComponent,
}: {
  items: ReadItem[];
  LinkComponent?: React.ElementType | undefined;
}) {
  const A = LinkComponent ?? "a";
  if (items.length === 0) return null;

  return (
    <div className="flex flex-col">
      {items.map((item, i) => (
        <A
          key={item.key}
          href={item.href}
          className={`group block border-[var(--color-border-default)] py-3.5 ${
            i === 0 ? "pt-0" : ""
          } ${i === items.length - 1 ? "pb-0" : "border-b"}`}
        >
          {/* Label above, figure below, both flush left — the same stacking as
              a StatRibbon cell. The earlier version put the label left and the
              value right on one baseline, which looks tidy in a mockup and is
              ragged with real data: a trailing unit shifts every number to a
              different x, so four figures in a column line up on nothing. */}
          <span className="block font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
            {item.label}
          </span>
          <span className="mt-1 flex items-baseline gap-1.5">
            {/* A step below the scale ribbon's figures, deliberately. These are
                secondary reads in a narrow column, and at the ribbon's size
                they out-shouted both it and the health prose beside them. */}
            <span className="text-lg font-semibold leading-none tracking-tight tabular-nums text-[var(--color-text-primary)] transition-colors group-hover:text-[var(--color-accent-primary)]">
              {item.value}
            </span>
            {item.unit && (
              <span className="text-[11px] font-medium text-[var(--color-text-tertiary)]">
                {item.unit}
              </span>
            )}
          </span>

          {item.bar && item.bar.length > 0 && (
            <span className="mt-2 flex h-1 w-full overflow-hidden rounded-full bg-[var(--color-bg-inset)]">
              {item.bar.map((seg, si) => (
                <span
                  key={si}
                  title={seg.title}
                  className="h-full"
                  style={{
                    width: `${Math.max(0, Math.min(1, seg.fraction)) * 100}%`,
                    background: seg.color,
                  }}
                />
              ))}
            </span>
          )}

          <span className="mt-1.5 block text-[11px] leading-relaxed text-[var(--color-text-tertiary)] [text-wrap:pretty]">
            {item.why}
          </span>
        </A>
      ))}
    </div>
  );
}
