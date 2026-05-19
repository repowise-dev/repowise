"use client";

import { HeartPulse, TestTubeDiagonal, Wrench, TrendingUp } from "lucide-react";

export type HealthTabKey = "overview" | "trend" | "coverage" | "refactoring";

export interface HealthTabsProps {
  repoId: string;
  active: HealthTabKey;
  /** Override the URL prefix the tabs build hrefs against. Defaults to
   *  `/repos/${repoId}/health`. Consumers with a different URL shape
   *  (snapshot-scoped, owner/name-scoped, …) pass their own prefix. */
  basePath?: string;
  /** Optional render-prop that produces a `<a href>` / `<Link>`. Lets the
   *  caller plug Next's `<Link>` without dragging the next dep into ui/. */
  renderLink?: (props: {
    href: string;
    label: string;
    active: boolean;
    icon: React.ReactNode;
    key: string;
  }) => React.ReactNode;
}

const TABS: { key: HealthTabKey; label: string; suffix: string; Icon: React.ComponentType<{ className?: string }> }[] = [
  { key: "overview", label: "Overview", suffix: "", Icon: HeartPulse },
  { key: "trend", label: "Trend", suffix: "/trend", Icon: TrendingUp },
  { key: "coverage", label: "Coverage", suffix: "/coverage", Icon: TestTubeDiagonal },
  { key: "refactoring", label: "Refactoring", suffix: "/refactoring-targets", Icon: Wrench },
];

export function HealthTabs({ repoId, active, basePath, renderLink }: HealthTabsProps) {
  const prefix = basePath ?? `/repos/${repoId}/health`;
  return (
    <div className="border-b border-[var(--color-border-default)]">
      <nav className="-mb-px flex flex-wrap gap-1" aria-label="Code health views">
        {TABS.map((t) => {
          const href = `${prefix}${t.suffix}`;
          const isActive = active === t.key;
          const baseCls =
            "inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium border-b-2 transition-colors";
          const activeCls = isActive
            ? "border-emerald-500 text-[var(--color-text-primary)]"
            : "border-transparent text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] hover:border-[var(--color-border-default)]";
          const icon = <t.Icon className="h-4 w-4" />;
          if (renderLink) {
            return renderLink({
              href,
              label: t.label,
              active: isActive,
              icon,
              key: t.key,
            });
          }
          return (
            <a key={t.key} href={href} className={`${baseCls} ${activeCls}`}>
              {icon}
              {t.label}
            </a>
          );
        })}
      </nav>
    </div>
  );
}
