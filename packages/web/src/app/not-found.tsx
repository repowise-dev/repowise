import Link from "next/link";
import { ChevronLeft } from "lucide-react";
import { getTranslations } from "next-intl/server";
import { RouteNotFound } from "@repowise-dev/ui/shared/route-states";

export default async function NotFound() {
  const t = await getTranslations("errors");

  return (
    <RouteNotFound
      title={t("notFoundTitle")}
      description={t("notFoundBody")}
      back={
        <Link href="/">
          <ChevronLeft className="h-4 w-4" aria-hidden />
          {t("backToDashboard")}
        </Link>
      }
    />
  );
}
