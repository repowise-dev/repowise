import type { ReactNode } from "react";
import { cn } from "@/lib/utils/cn";

/**
 * Settings, grouped by what people manage, in this order. Privacy sits last.
 * The global page and the repo page use the same names, and each shows the
 * groups it has something for.
 */
export const SETTINGS_GROUPS = [
  "setup",
  "agents",
  "models",
  "sync",
  "coverage",
  "server",
  "privacy",
] as const;

export type SettingsGroupId = (typeof SETTINGS_GROUPS)[number];

/** Every group's title, from `settings.groups.<id>.title`. */
export function settingsGroupTitles(t: (key: string) => string): Record<SettingsGroupId, string> {
  return Object.fromEntries(SETTINGS_GROUPS.map((g) => [g, t(`${g}.title`)])) as Record<
    SettingsGroupId,
    string
  >;
}

export const settingsGroupAnchor =(id: SettingsGroupId) => `group-${id}`;

/** In-page links to the groups a page shows; wraps instead of scrolling sideways. */
export function SettingsGroupNav({
  groups,
  label,
  titles,
}: {
  groups: readonly SettingsGroupId[];
  label: string;
  titles: Record<SettingsGroupId, string>;
}) {
  return (
    <nav aria-label={label} className="-mt-2">
      <ul className="flex flex-wrap gap-1.5">
        {SETTINGS_GROUPS.filter((g) => groups.includes(g)).map((g) => (
          <li key={g}>
            <a
              href={`#${settingsGroupAnchor(g)}`}
              className="inline-flex rounded-md px-2 py-1 text-xs text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-wash)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
            >
              {titles[g]}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}

/** One group: a heading the nav links to, then the sections it holds, unchanged. */
export function SettingsGroup({
  id,
  title,
  description,
  children,
  className,
}: {
  id: SettingsGroupId;
  title: string;
  description?: string;
  children: ReactNode;
  className?: string;
}) {
  const anchor = settingsGroupAnchor(id);
  return (
    <section
      id={anchor}
      aria-labelledby={`${anchor}-title`}
      className={cn("flex scroll-mt-24 flex-col gap-6 sm:gap-8", className)}
    >
      <header className="-mb-2 border-t-2 border-[var(--color-border-hover)] pt-4">
        <h2
          id={`${anchor}-title`}
          className="font-mono text-[11px] uppercase tracking-[0.1em] text-[var(--color-text-tertiary)]"
        >
          {title}
        </h2>
        {description && (
          <p className="mt-1 max-w-[62ch] text-xs leading-relaxed text-[var(--color-text-tertiary)] [text-wrap:pretty]">
            {description}
          </p>
        )}
      </header>
      {children}
    </section>
  );
}
