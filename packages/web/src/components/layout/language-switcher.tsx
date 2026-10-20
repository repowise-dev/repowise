"use client";

/**
 * LanguageSwitcher — one compact button showing the current language that
 * opens a menu of every locale in `LOCALES`.
 *
 * Switching is cookie-first: `NEXT_LOCALE` is what the server reads on the
 * next request, so writing it and calling `router.refresh()` re-renders the
 * server tree in the new language without a full page load (scroll position
 * and route state survive). `localStorage` mirrors the cookie so the choice
 * survives a cookie clear.
 *
 * There is no URL prefix involved, which is the point: every existing link,
 * bookmark and uptime probe keeps resolving to the same page.
 */

import * as React from "react";
import { useLocale, useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { Check, Globe } from "lucide-react";
import { Popover, PopoverContent, PopoverTrigger } from "@repowise-dev/ui/ui/popover";
import { toast } from "sonner";
import { cn } from "@/lib/utils/cn";
import {
  LOCALES,
  LOCALE_COOKIE,
  LOCALE_LABELS,
  LOCALE_STORAGE_KEY,
  resolveLocale,
  type Locale,
} from "@/i18n/config";

/** One year, in seconds. */
const COOKIE_MAX_AGE = 60 * 60 * 24 * 365;

/** Short marks for the trigger, in each language's own script. */
const SHORT_LABEL: Record<Locale, string> = {
  en: "EN",
  "zh-CN": "中文",
};

export interface LanguageSwitcherProps {
  /** Globe only, for the 56px collapsed sidebar where the mark does not fit. */
  compact?: boolean;
  className?: string;
}

export function LanguageSwitcher({ compact, className }: LanguageSwitcherProps) {
  const [open, setOpen] = React.useState(false);
  const active = useLocale() as Locale;
  const t = useTranslations("language");
  const router = useRouter();
  const [pending, startTransition] = React.useTransition();

  // Restore from the localStorage mirror when the cookie is gone (cleared
  // cookies, or a first visit after the user switched on another profile).
  // Runs once per page load, and only when the two disagree — otherwise it
  // would re-refresh forever.
  const restored = React.useRef(false);
  React.useEffect(() => {
    if (restored.current) return;
    restored.current = true;
    let stored: string | null = null;
    try {
      stored = window.localStorage.getItem(LOCALE_STORAGE_KEY);
    } catch {
      return;
    }
    if (!stored) return;
    const target = resolveLocale(stored);
    const cookieAlreadySet = document.cookie
      .split("; ")
      .some((c) => c.startsWith(`${LOCALE_COOKIE}=`));
    if (cookieAlreadySet || target === active) return;
    document.cookie = cookieFor(target);
    document.documentElement.lang = target;
    startTransition(() => router.refresh());
    // `active` is intentionally excluded: this is a one-shot restore, not a
    // reaction to the locale the server just rendered.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const select = (next: Locale) => {
    setOpen(false);
    if (next === active || pending) return;
    document.cookie = cookieFor(next);
    try {
      window.localStorage.setItem(LOCALE_STORAGE_KEY, next);
    } catch {
      /* localStorage unavailable — the cookie still carries the choice. */
    }
    // Keep <html lang> right immediately; the refresh below re-renders it
    // from the server value anyway.
    document.documentElement.lang = next;
    toast.success(t("switched", { language: LOCALE_LABELS[next] }));
    startTransition(() => router.refresh());
  };

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          aria-label={`${t("label")}: ${LOCALE_LABELS[active]}`}
          className={cn(
            "inline-flex h-8 min-w-8 items-center justify-center gap-1 rounded-md px-1.5 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:bg-[var(--color-bg-wash-hover)] hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]",
            className,
          )}
        >
          <Globe className="h-3.5 w-3.5 shrink-0" aria-hidden />
          {!compact && <span lang={active}>{SHORT_LABEL[active]}</span>}
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" side="top" className="w-44 p-1">
        <ul aria-label={t("label")} className="flex flex-col">
          {LOCALES.map((locale) => {
            const selected = locale === active;
            return (
              <li key={locale}>
                <button
                  type="button"
                  aria-current={selected ? "true" : undefined}
                  lang={locale}
                  onClick={() => select(locale)}
                  className="flex min-h-8 w-full items-center justify-between gap-2 rounded px-2 text-left text-sm text-[var(--color-text-primary)] hover:bg-[var(--color-bg-wash-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-primary)]"
                >
                  {LOCALE_LABELS[locale]}
                  {selected && <Check className="h-3.5 w-3.5" aria-hidden />}
                </button>
              </li>
            );
          })}
        </ul>
      </PopoverContent>
    </Popover>
  );
}

function cookieFor(locale: Locale): string {
  return `${LOCALE_COOKIE}=${encodeURIComponent(locale)}; path=/; max-age=${COOKIE_MAX_AGE}; samesite=lax`;
}
