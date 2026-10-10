import Link from "next/link";
import { ChevronLeft, Home } from "lucide-react";
import { getTranslations } from "next-intl/server";
import { RouteNotFound } from "@repowise-dev/ui/shared/route-states";

export default async function RepoNotFound() {
  const t = await getTranslations("errors");

  return (
    <RouteNotFound
      title={t("repoNotFoundTitle")}
      description={t("repoNotFoundBody")}
      icon={<Home />}
      back={
        <Link href="/">
          <ChevronLeft className="h-4 w-4" aria-hidden />
          {t("backToDashboard")}
        </Link>
      }
    />
  );
}
