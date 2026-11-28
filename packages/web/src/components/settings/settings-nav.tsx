"use client";

import { useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { cn } from "@/lib/utils/cn";
import { groupFromHash, type SettingsGroupId } from "./settings-groups";

/**
 * The settings sub-pages: a left rail on wide screens, a wrapping row of
 * tabs below `lg` so nothing scrolls sideways at 390px.
 */
export function SettingsNav({
  base,
  groups,
  titles,
  label,
}: {
  base: string;
  groups: readonly SettingsGroupId[];
  titles: Record<SettingsGroupId, string>;
  label: string;
}) {
  const pathname = usePathname() ?? "";
  return (
    <nav aria-label={label} className="lg:sticky lg:top-6 lg:w-44 lg:shrink-0 lg:self-start">
      <ul className="flex flex-wrap gap-1 lg:flex-col lg:gap-0.5">
        {groups.map((g) => {
          const href = `${base}/${g}`;
          const active = pathname === href || pathname.startsWith(`${href}/`);
          return (
            <li key={g}>
              <Link
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex rounded-md px-2.5 py-1.5 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]",
                  active
                    ? "bg-[var(--color-bg-wash)] font-medium text-[var(--color-text-primary)]"
                    : "text-[var(--color-text-secondary)] hover:bg-[var(--color-bg-wash)] hover:text-[var(--color-text-primary)]",
                )}
              >
                {titles[g]}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/**
 * The bare settings route: opens the sub-page an old `#group-<id>` or
 * `#provider` link meant, else the first group. Client side, because the
 * hash never reaches the server.
 */
export function SettingsRedirect({
  base,
  groups,
}: {
  base: string;
  groups: readonly SettingsGroupId[];
}) {
  const router = useRouter();
  useEffect(() => {
    const group = groupFromHash(window.location.hash, groups) ?? groups[0];
    router.replace(`${base}/${group}`);
  }, [base, groups, router]);
  return null;
}
