"use client";

import { HealthTabs, type HealthTabKey, type HealthTabsProps } from "./health-tabs";
import { RelativeTime } from "../shared/relative-time";

export interface HealthPageChromeProps {
  repoId: string;
  active: HealthTabKey;
  title: string;
  subtitle?: React.ReactNode;
  icon?: React.ReactNode;
  meta?: {
    last_indexed_at: string | null;
    head_commit: string | null;
    snapshot_count: number;
  } | null;
  actions?: React.ReactNode;
  /** Forwarded to `HealthTabs`. Overrides the default `/repos/${repoId}/health`
   *  prefix the tabs build hrefs against. */
  basePath?: string;
  /** Forwarded to `HealthTabs`. Lets the caller plug a framework-specific
   *  link (e.g. Next.js `<Link>`) without dragging that dep into ui/. */
  renderLink?: HealthTabsProps["renderLink"];
}

/** "Indexed 2d ago · abc1234 · 3 snapshots" under the page title. */
function IndexedMeta({ meta }: { meta: NonNullable<HealthPageChromeProps["meta"]> }) {
  return (
    <p className="mt-1 text-xs text-[var(--color-text-tertiary)]">
      Indexed{" "}
      {meta.last_indexed_at ? <RelativeTime date={meta.last_indexed_at} /> : "never"}
      {meta.head_commit ? (
        <>
          {" "}
          · <span className="font-mono">{meta.head_commit.slice(0, 7)}</span>
        </>
      ) : null}
      {meta.snapshot_count > 0 ? <> · {meta.snapshot_count} snapshots</> : null}
    </p>
  );
}

export function HealthPageChrome({
  repoId,
  active,
  title,
  subtitle,
  icon,
  meta,
  actions,
  basePath,
  renderLink,
}: HealthPageChromeProps) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold text-[var(--color-text-primary)] mb-1 flex items-center gap-2">
            {icon}
            {title}
          </h1>
          {subtitle ? (
            <p className="text-sm text-[var(--color-text-secondary)]">{subtitle}</p>
          ) : null}
          {meta ? <IndexedMeta meta={meta} /> : null}
        </div>
        {actions ? <div className="flex items-center gap-2 shrink-0">{actions}</div> : null}
      </div>
      <HealthTabs
        repoId={repoId}
        active={active}
        {...(basePath !== undefined ? { basePath } : {})}
        {...(renderLink !== undefined ? { renderLink } : {})}
      />
    </div>
  );
}
