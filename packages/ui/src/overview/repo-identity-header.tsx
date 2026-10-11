import * as React from "react";
import { RepoAvatar } from "./repo-avatar";

export interface RepoIdentityMeta {
  /** Short label, e.g. "main", "ab13c4e", "419.6K lines". */
  label: string;
  /** Renders an accent dot before the label — used for the primary language. */
  dot?: boolean;
  /** Native tooltip, e.g. the absolute timestamp behind "synced 1 day ago". */
  title?: string;
  /** Renders monospaced. Use for machine-produced values: SHAs, counts, paths. */
  mono?: boolean;
}

export interface RepoIdentityHeaderProps {
  name: string;
  /** Owner segment, rendered in front of the name in a quieter weight.
   *  Hosted has one; the OSS CLI usually does not, so it is optional. */
  owner?: string | null;
  /** One or two sentences about the repo. This is the whole orientation job
   *  for a first-time visitor, which matters because OSS users never see the
   *  public repo landing page — this page is the only description they get. */
  description?: string | null;
  /** Git remote, used only to resolve a GitHub avatar. */
  remoteUrl?: string | null;
  meta?: RepoIdentityMeta[];
  /** Primary action plus an overflow trigger. One everyday action, not three
   *  equal buttons: sync is navigation, re-index is maintenance. */
  actions?: React.ReactNode;
}

/**
 * Repo identity band at the top of the Overview.
 *
 * Replaces PageShell's generic title/description header on this page only,
 * because Overview's header carries real content (mark, owner, description,
 * language, freshness) rather than a label. It still renders exactly one `h1`,
 * so the page keeps a single subject.
 */
export function RepoIdentityHeader({
  name,
  owner,
  description,
  remoteUrl,
  meta = [],
  actions,
}: RepoIdentityHeaderProps) {
  return (
    <header className="flex flex-col gap-4 sm:flex-row sm:items-start sm:gap-4">
      <RepoAvatar name={name} remoteUrl={remoteUrl} size={40} className="hidden sm:inline-flex" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-3">
          <RepoAvatar name={name} remoteUrl={remoteUrl} size={32} className="sm:hidden" />
          <h1 className="min-w-0 text-xl font-semibold tracking-tight text-[var(--color-text-primary)] sm:text-2xl">
            {owner && (
              <span className="font-normal text-[var(--color-text-tertiary)]">{owner} / </span>
            )}
            <span className="[overflow-wrap:anywhere]">{name}</span>
          </h1>
        </div>
        {description && (
          // `overflow-wrap:anywhere` because callers pass a filesystem path
          // here, and a path is one unbreakable token: without it a long
          // absolute path overflows on a phone and takes the page body with it.
          <p className="mt-1.5 max-w-[64ch] text-sm text-[var(--color-text-secondary)] [overflow-wrap:anywhere] [text-wrap:pretty]">
            {description}
          </p>
        )}
        {meta.length > 0 && (
          <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-[var(--color-text-tertiary)]">
            {meta.map((m) => (
              <span
                key={m.label}
                title={m.title}
                className={`inline-flex items-center ${m.mono ? "font-mono tabular-nums" : ""}`}
              >
                {m.dot && (
                  <span
                    aria-hidden
                    className="mr-1.5 inline-block h-1.5 w-1.5 rounded-full bg-[var(--color-accent-fill)]"
                  />
                )}
                {m.label}
              </span>
            ))}
          </div>
        )}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </header>
  );
}
