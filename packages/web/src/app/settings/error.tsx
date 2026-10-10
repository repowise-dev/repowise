"use client";

import { useEffect } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ArrowLeft } from "lucide-react";
import { RouteError } from "@repowise-dev/ui/shared/route-states";

export default function SettingsError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const t = useTranslations("errors");

  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <RouteError
      title={t("settingsErrorTitle")}
      message={t("settingsErrorFallback")}
      digest={error.digest}
      digestLabel={t("digest")}
      onRetry={reset}
      retryLabel={t("retry")}
      back={
        <Link href="/">
          <ArrowLeft className="h-3.5 w-3.5" aria-hidden />
          {t("dashboard")}
        </Link>
      }
    />
  );
}
