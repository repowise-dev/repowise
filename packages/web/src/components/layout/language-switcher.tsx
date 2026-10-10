"use client";

/**
 * LanguageSwitcher — the 中 / EN control in the shell tool area.
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
import { Languages } from "lucide-react";
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

/** Short marks for the compact control, in each language's own script. */
const SHORT_LABEL: Record<Locale, string> = {
  en: "EN",
  "zh-CN": "中",
};

export interface LanguageSwitcherProps {
  /** Icon-scale control: short marks and no icon, for the sidebar footer. */
  compact?: boolean;
  className?: string;
}

export function LanguageSwitcher({ compact = false, className }: LanguageSwitcherProps) {
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
    <div
      role="radiogroup"
      aria-label={t("label")}
      className={cn("inline-flex items-center gap-1 rounded-lg p-0.5", className)}
    >
      {!compact && (
        <Languages
          className="mx-0.5 h-3.5 w-3.5 shrink-0 text-[var(--color-text-tertiary)]"
          aria-hidden
        />
      )}
      {LOCALES.map((locale) => {
        const selected = locale === active;
        return (
          <button
            key={locale}
            type="button"
            role="radio"
            aria-checked={selected}
            aria-label={LOCALE_LABELS[locale]}
            title={LOCALE_LABELS[locale]}
            onClick={() => select(locale)}
            className={cn(
              "inline-flex items-center justify-center rounded-md text-xs font-medium transition-colors",
              compact ? "px-1.5 py-1" : "px-2.5 py-1.5",
              selected
                ? "bg-[var(--color-bg-surface)] text-[var(--color-text-primary)] shadow-[var(--shadow-sm)]"
                : "bg-transparent text-[var(--color-text-secondary)] shadow-none hover:text-[var(--color-text-primary)]",
            )}
          >
            {compact ? SHORT_LABEL[locale] : LOCALE_LABELS[locale]}
          </button>
        );
      })}
    </div>
  );
}

function cookieFor(locale: Locale): string {
  return `${LOCALE_COOKIE}=${encodeURIComponent(locale)}; path=/; max-age=${COOKIE_MAX_AGE}; samesite=lax`;
}
