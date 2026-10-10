"use client";

import { useEffect } from "react";
import { useTranslations } from "next-intl";
import { RouteError } from "@repowise-dev/ui/shared/route-states";

export default function GlobalError({
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
      title={t("errorTitle")}
      message={t("errorFallback")}
      digest={error.digest}
      digestLabel={t("digest")}
      onRetry={reset}
      retryLabel={t("tryAgain")}
    />
  );
}
