"use client";

import Link from "next/link";
import { HealthTabs, type HealthTabKey } from "@repowise-dev/ui/health";

interface HealthPageChromeProps {
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
}

function formatIndexedAt(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  const diff = Date.now() - d.getTime();
  const minutes = Math.round(diff / 60_000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d ago`;
  return d.toLocaleDateString();
}

export function HealthPageChrome({
  repoId,
  active,
  title,
  subtitle,
  icon,
  meta,
  actions,
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
          {meta ? (
            <p className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">
              Indexed {formatIndexedAt(meta.last_indexed_at)}
              {meta.head_commit ? (
                <>
                  {" "}
                  · <span className="font-mono">{meta.head_commit.slice(0, 7)}</span>
                </>
              ) : null}
              {meta.snapshot_count > 0 ? <> · {meta.snapshot_count} snapshots</> : null}
            </p>
          ) : null}
        </div>
        {actions ? <div className="flex items-center gap-2 shrink-0">{actions}</div> : null}
      </div>
      <HealthTabs
        repoId={repoId}
        active={active}
        renderLink={({ href, label, active: isActive, icon, key }) => (
          <Link
            key={key}
            href={href}
            className={
              "inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium border-b-2 transition-colors " +
              (isActive
                ? "border-emerald-500 text-[var(--color-text-primary)]"
                : "border-transparent text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] hover:border-[var(--color-border-default)]")
            }
          >
            {icon}
            {label}
          </Link>
        )}
      />
    </div>
  );
}
