"use client";

import { useTranslations } from "next-intl";

/** Dot plus "New" beside a nav label for a page the latest upgrade added or
 *  changed. `dotOnly` for the collapsed icon rail, with the word kept for
 *  screen readers. */
export function NewMark({ dotOnly = false }: { dotOnly?: boolean }) {
  const t = useTranslations("shell");
  if (dotOnly) {
    return (
      <span className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-[var(--color-info)]">
        <span className="sr-only">{t("newMark")}</span>
      </span>
    );
  }
  return (
    <span className="ml-auto inline-flex shrink-0 items-center gap-1 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-info)]">
      <span aria-hidden className="h-1.5 w-1.5 rounded-full bg-[var(--color-info)]" />
      {t("newMark")}
    </span>
  );
}
