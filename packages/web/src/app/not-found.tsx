import Link from "next/link";
import { LayoutDashboard } from "lucide-react";
import { getTranslations } from "next-intl/server";
import { RouteNotFound } from "@repowise-dev/ui/shared/route-states";

export default async function NotFound() {
  const t = await getTranslations("errors");

  return (
    <RouteNotFound
      title={t("notFoundTitle")}
      description={t("notFoundBody")}
      linksLabel={t("notFoundLinks")}
      links={[
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
