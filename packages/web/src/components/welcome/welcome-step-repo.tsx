"use client";

import Link from "next/link";
import type { ReactNode } from "react";
import { useFormatter, useTranslations } from "next-intl";
import { seriesColor } from "@repowise-dev/ui/shared/series";
import type { HealthBand } from "@repowise-dev/types/health";
import { reviewHref } from "@/lib/changes";
import type { WelcomeLens } from "@/lib/welcome-moment";
import {
  MOMENT_CAPTION,
  MOMENT_CARD,
  MOMENT_FIGURE,
  MOMENT_LINK,
  MOMENT_MICRO,
  MomentLabel,
} from "@/components/layout/moment-shell";

/** Series index per lens, as the design colours them. */
const SERIES_OF: Record<WelcomeLens["key"], number> = {
  health: 2,
  deadCode: 3,
  risk: 4,
  decisions: 1,
  docs: 5,
  tests: 0,
  testsInferred: 0,
};

const BAND_DOT: Record<HealthBand, string> = {
  excellent: "var(--color-success)",
  good: "var(--color-success)",
  fair: "var(--color-warning)",
  needs_work: "var(--color-warning)",
  at_risk: "var(--color-error)",
};

interface CardText {
  label: string;
  value: string;
  unit?: ReactNode;
  caption: ReactNode;
  href: string;
  open: string;
}

/** Step 1: one card per lens the index measured. An odd last card spans the
 *  row, so the grid never ends on a gap. */
export function WelcomeStepRepo({
  repoId,
  lenses,
  onNavigate,
}: {
  repoId: string;
  lenses: WelcomeLens[];
  onNavigate: () => void;
}) {
  const t = useTranslations("welcome.repo");
  const text = useLensText(repoId);
  if (lenses.length === 0) return null;
  return (
    <section aria-labelledby="welcome-standout">
      <h3 id="welcome-standout" className={`${MOMENT_MICRO} mb-3`}>
        {t("standout")}
      </h3>
      <ul className="grid grid-cols-1 gap-3 min-[561px]:grid-cols-2 min-[561px]:[&>li:last-child:nth-child(odd)]:col-span-2">
        {lenses.map((lens) => {
          const c = text(lens);
          return (
            <li key={lens.key} className={`${MOMENT_CARD} flex min-w-0 flex-col p-4`}>
              <MomentLabel color={seriesColor(SERIES_OF[lens.key])}>{c.label}</MomentLabel>
              <p className="mt-2 flex items-baseline gap-2">
                <span className={MOMENT_FIGURE}>{c.value}</span>
                {c.unit && <span className="text-xs text-[var(--color-text-secondary)]">{c.unit}</span>}
              </p>
              <p className={`${MOMENT_CAPTION} mt-1 [overflow-wrap:anywhere]`}>{c.caption}</p>
              <Link href={c.href} onClick={onNavigate} className={`${MOMENT_LINK} mt-auto self-start pt-3`}>
                {c.open}
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

const mono = (chunks: ReactNode) => <span className="font-mono text-[0.95em]">{chunks}</span>;

/** The words and route for each lens. */
function useLensText(repoId: string): (lens: WelcomeLens) => CardText {
  const t = useTranslations("welcome.repo");
  const format = useFormatter();
  const n = (v: number) => format.number(v);
  const base = `/repos/${encodeURIComponent(repoId)}`;
  return (lens) => {
    switch (lens.key) {
      case "health":
        return {
          label: t("health.label"),
          value: lens.score.toFixed(1),
          unit: lens.band && (
            <span className="inline-flex items-center gap-1.5">
              <span aria-hidden className="size-1.5 rounded-full" style={{ background: BAND_DOT[lens.band] }} />
              {t(`band.${lens.band}`)}
            </span>
          ),
          caption:
            lens.worstPath && lens.worstScore != null
              ? t.rich("health.caption", { path: lens.worstPath, score: lens.worstScore.toFixed(1), mono })
              : t("health.captionNone"),
          href: `${base}/code-health`,
          open: t("health.open"),
        };
      case "deadCode":
        return {
          label: t("deadCode.label"),
          value: n(lens.files),
          unit: t("deadCode.unit", { count: lens.files }),
          caption:
            lens.files === 0
              ? t("deadCode.captionNone")
              : `${t("deadCode.caption")}${lens.safe > 0 ? ` ${t("deadCode.safe", { count: lens.safe })}` : ""}`,
          href: `${base}/code-health?tab=dead-code`,
          open: t("deadCode.open"),
        };
      case "risk":
        return {
          label: t("risk.label"),
          value: n(lens.reached),
          unit: t("risk.unit", { count: lens.reached }),
          caption: t.rich("risk.caption", { path: lens.path, mono }),
          href: reviewHref(repoId),
          open: t("risk.open"),
        };
      case "decisions":
        return {
          label: t("decisions.label"),
          value: n(lens.count),
          unit: t("decisions.unit"),
          caption: t("decisions.caption"),
          href: `${base}/decisions`,
          open: t("decisions.open"),
        };
      case "docs":
        return {
          label: t("docs.label"),
          value: n(lens.pages),
          unit: t("docs.unit", { count: lens.pages }),
          caption: t("docs.caption"),
          href: `${base}/docs`,
          open: t("docs.open"),
        };
      case "tests":
        return {
          label: t("tests.label"),
          value: `${lens.measuredPct.toFixed(0)}%`,
          unit: t("tests.measured"),
          caption: t("tests.measuredCaption"),
          href: `${base}/tests`,
          open: t("tests.open"),
        };
      case "testsInferred":
        return {
          label: t("tests.label"),
          value: n(lens.reached),
          unit: t("tests.inferredUnit", { total: n(lens.total) }),
          caption: t("tests.inferredCaption"),
          href: `${base}/tests`,
          open: t("tests.open"),
        };
    }
  };
}
