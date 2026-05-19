"use client";

import Link from "next/link";
import {
  HealthPageChrome as BaseHealthPageChrome,
  type HealthPageChromeProps as BaseHealthPageChromeProps,
} from "@repowise-dev/ui/health";

export type HealthPageChromeProps = Omit<BaseHealthPageChromeProps, "renderLink">;

/** Web binding for the shared `HealthPageChrome` — injects Next's `<Link>`
 *  so health-tab navigation is client-routed instead of hard-reloading. */
export function HealthPageChrome(props: HealthPageChromeProps) {
  return (
    <BaseHealthPageChrome
      {...props}
      renderLink={({ href, label, active, icon, key }) => (
        <Link
          key={key}
          href={href}
          className={
            "inline-flex items-center gap-1.5 px-3 py-2 text-sm font-medium border-b-2 transition-colors " +
            (active
              ? "border-emerald-500 text-[var(--color-text-primary)]"
              : "border-transparent text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)] hover:border-[var(--color-border-default)]")
          }
        >
          {icon}
          {label}
        </Link>
      )}
    />
  );
}
