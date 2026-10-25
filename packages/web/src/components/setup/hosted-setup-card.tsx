"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { X } from "lucide-react";
import { Button } from "@repowise-dev/ui/ui/button";
import { config } from "@/lib/config";
import { hostedLink } from "@/lib/hosted";
import type { OnboardingState } from "@/lib/api/setup";
import { useHideOnThisMachine } from "./use-setup";

/** `src=local_web_setup_card` on both links. */
export const HOSTED_CARD_SURFACE = "setup_card";

const ITEMS = ["push", "reviews", "dashboard", "webApps"] as const;

/**
 * Shown once the essentials are done, unless tips are off (`repowise config
 * hints off`, `REPOWISE_NO_HINTS`, `DO_NOT_TRACK`, or this browser's tips
 * switch), the person is signed in, or it was hidden on this machine.
 */
export function hostedCardVisible(onboarding: OnboardingState, tipsHiddenHere: boolean): boolean {
  return (
    onboarding.hints_enabled &&
    !onboarding.signed_in &&
    !onboarding.hosted_card_hidden &&
    !tipsHiddenHere
  );
}

export function HostedSetupCard({ onboarding }: { onboarding: OnboardingState }) {
  const t = useTranslations("setup.hosted");
  const { hide, hiddenNow } = useHideOnThisMachine();
  // Off until read after mount, so SSR and the first client render agree.
  const [tipsHiddenHere, setTipsHiddenHere] = useState(true);
  useEffect(() => setTipsHiddenHere(config.getHostedTipsHidden()), []);

  if (hiddenNow.has("hosted_card_hidden") || !hostedCardVisible(onboarding, tipsHiddenHere)) {
    return null;
  }
  return (
    <section
      aria-labelledby="hosted-setup-card-title"
      className="mt-6 rounded-[10px] border border-[var(--color-border-default)] border-l-[3px] border-l-[var(--color-info)] bg-[var(--color-release-card)] px-4 py-3.5"
    >
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-[10px] uppercase tracking-[0.1em] text-[var(--color-text-tertiary)]">
          {t("eyebrow")}
        </span>
        <button
          type="button"
          onClick={() => void hide("hosted_card_hidden")}
          aria-label={t("hide")}
          title={t("hide")}
          className="-m-1.5 rounded p-1.5 text-[var(--color-text-tertiary)] transition-colors hover:text-[var(--color-text-primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-focus-ring)]"
        >
          <X className="h-4 w-4" />
        </button>
      </div>
      <h2 id="hosted-setup-card-title" className="mt-1 text-[15px] font-semibold text-[var(--color-text-primary)]">
        {t("title")}
      </h2>
      <p className="text-xs text-[var(--color-text-secondary)]">{t("lede")}</p>
      <ul className="my-3 grid grid-cols-1 gap-x-4 gap-y-1.5 text-xs text-[var(--color-text-secondary)] sm:grid-cols-2">
        {ITEMS.map((item) => (
          <li key={item} className="flex items-baseline gap-2">
            <span aria-hidden className="inline-block h-[7px] w-[7px] shrink-0 rounded-full bg-[var(--color-info)]" />
            {t(`items.${item}`)}
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-3">
        <Button asChild size="sm">
          <a href={hostedLink(HOSTED_CARD_SURFACE, "start")} target="_blank" rel="noopener noreferrer">
            {t("start")}
          </a>
        </Button>
        <a
          href={hostedLink(HOSTED_CARD_SURFACE, "team")}
          target="_blank"
          rel="noopener noreferrer"
          className="text-xs font-medium text-[var(--color-text-primary)] underline decoration-[var(--color-border-hover)] underline-offset-[3px] hover:decoration-[var(--color-text-primary)]"
        >
          {t("see")}
        </a>
      </div>
    </section>
  );
}
