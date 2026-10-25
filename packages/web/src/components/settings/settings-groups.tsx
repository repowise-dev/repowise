import type { ReactNode } from "react";
import { cn } from "@/lib/utils/cn";

/**
 * Settings, grouped by what people manage, in this order. Privacy sits last.
 * Each group is its own sub-page (`/settings/<group>`,
 * `/repos/<id>/settings/<group>`), and each surface shows the groups it has
 * something for.
 */
export const SETTINGS_GROUPS = [
  "setup",
  "general",
  "agents",
  "models",
  "sync",
  "coverage",
  "server",
  "display",
  "privacy",
] as const;

export type SettingsGroupId = (typeof SETTINGS_GROUPS)[number];

/** General and Coverage are per repository, so global settings has neither. */
export const GLOBAL_SETTINGS_GROUPS: readonly SettingsGroupId[] = [
  "setup",
  "agents",
  "models",
  "sync",
  "server",
  "display",
  "privacy",
];

/** Server, display and telemetry are per machine, so repo settings has none of them. */
export const REPO_SETTINGS_GROUPS: readonly SettingsGroupId[] = [
  "setup",
  "general",
  "agents",
  "models",
  "sync",
  "coverage",
];

export const GLOBAL_SETTINGS_BASE = "/settings";

export function repoSettingsBase(repoId: string): string {
  return `/repos/${encodeURIComponent(repoId)}/settings`;
}

/** A global settings sub-page. */
export function settingsHref(group: SettingsGroupId): string {
  return `${GLOBAL_SETTINGS_BASE}/${group}`;
}

/** One repository's settings sub-page. */
export function repoSettingsHref(repoId: string, group: SettingsGroupId): string {
  return `${repoSettingsBase(repoId)}/${group}`;
}

/** Anchors from when settings was one long page, and where each lives now. */
const LEGACY_ANCHORS: Record<string, SettingsGroupId> = { provider: "models" };

/**
 * The sub-page an old in-page link meant: `#group-<id>` or `#provider`. Null
 * when the hash names nothing this surface has.
 */
export function groupFromHash(hash: string, groups: readonly SettingsGroupId[]): SettingsGroupId | null {
  const anchor = hash.replace(/^#/, "");
  const id = LEGACY_ANCHORS[anchor] ?? anchor.replace(/^group-/, "");
  return (groups as readonly string[]).includes(id) ? (id as SettingsGroupId) : null;
}

/** Every group's title, from `settings.groups.<id>.title`. */
export function settingsGroupTitles(t: (key: string) => string): Record<SettingsGroupId, string> {
  return Object.fromEntries(SETTINGS_GROUPS.map((g) => [g, t(`${g}.title`)])) as Record<
    SettingsGroupId,
    string
  >;
}

/** One sub-page: its heading, one line on what it holds, then its rows. */
export function SettingsGroup({
  title,
  description,
  children,
  className,
}: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section aria-labelledby="settings-group-title" className={cn("flex flex-col gap-4", className)}>
      <header>
        <h2 id="settings-group-title" className="text-base font-semibold text-[var(--color-text-primary)]">
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

/** A titled block inside a group's Advanced, e.g. decision capture. */
export function SettingsBlock({
  title,
  description,
  children,
}: {
  title: string;
  description?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3 border-t border-[var(--color-border-default)] py-4 first:border-t-0 first:pt-1">
      <div>
        <h3 className="text-sm font-medium text-[var(--color-text-primary)]">{title}</h3>
        {description && (
          <p className="mt-0.5 max-w-[62ch] text-xs leading-relaxed text-[var(--color-text-tertiary)] [text-wrap:pretty]">
            {description}
          </p>
        )}
      </div>
      {children}
    </div>
  );
}

export const SETTINGS_LINK =
  "text-xs font-medium text-[var(--color-text-primary)] underline decoration-[var(--color-border-hover)] underline-offset-[3px] hover:decoration-[var(--color-text-primary)]";
