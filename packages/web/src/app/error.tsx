"use client";

import { useEffect } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";
import { useTranslations } from "next-intl";
import { Button } from "@repowise-dev/ui/ui/button";

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
    <div className="flex h-full min-h-[400px] flex-col items-center justify-center gap-4 p-6 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-[var(--color-error)]/10">
        <AlertTriangle className="h-6 w-6 text-[var(--color-error)]" />
      </div>
      <div>
        <h2 className="text-base font-semibold text-[var(--color-text-primary)]">
          {t("errorTitle")}
        </h2>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)] max-w-sm">
          {error.message || t("errorFallback")}
        </p>
        {error.digest && (
          <p className="mt-1 text-xs font-mono text-[var(--color-text-tertiary)]">
            {t("digest")}: {error.digest}
          </p>
        )}
      </div>
      <Button onClick={reset} size="sm" className="gap-2">
        <RefreshCw className="h-3.5 w-3.5" />
        {t("tryAgain")}
      </Button>
    </div>
  );
}
