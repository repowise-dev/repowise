"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { BookOpen, HeartPulse, Home, LayoutDashboard } from "lucide-react";
import { useTranslations } from "next-intl";
import { RouteNotFound } from "@repowise-dev/ui/shared/route-states";

export default function RepoNotFound() {
  const t = useTranslations("errors");
  const params = useParams<{ id?: string }>();
  const base = params?.id ? `/repos/${params.id}` : null;

  return (
    <RouteNotFound
      title={t("repoNotFoundTitle")}
      description={t("repoNotFoundBody")}
      linksLabel={t("notFoundLinks")}
      links={[
        ...(base
          ? [
              <Link key="overview" href={`${base}/overview`}>
                <Home aria-hidden />
                {t("repoOverview")}
              </Link>,
              <Link key="health" href={`${base}/code-health`}>
                <HeartPulse aria-hidden />
                {t("repoCodeHealth")}
              </Link>,
              <Link key="docs" href={`${base}/docs`}>
                <BookOpen aria-hidden />
                {t("repoDocs")}
              </Link>,
            ]
          : []),
        <Link key="dashboard" href="/">
          <LayoutDashboard aria-hidden />
          {t("dashboard")}
        </Link>,
      ]}
      hint={t.rich("notFoundSearchHint", {
        kbd: (chunks) => (
          <kbd className="rounded border border-[var(--color-border-default)] px-1 font-mono text-[10px]">
            {chunks}
          </kbd>
        ),
      })}
    />
  );
}
