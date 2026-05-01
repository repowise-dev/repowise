"use client";

import { useEffect } from "react";
import Link from "next/link";
import { AlertTriangle, RefreshCw, ArrowLeft } from "lucide-react";
import { Button } from "@repowise/ui/ui/button";

export default function RepoError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <div className="flex h-full min-h-[400px] flex-col items-center justify-center gap-4 p-6 text-center">
      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-red-500/10">
        <AlertTriangle className="h-6 w-6 text-red-500" />
      </div>
      <div>
        <h2 className="text-base font-semibold text-[var(--color-text-primary)]">
          Failed to load repository
        </h2>
        <p className="mt-1 text-sm text-[var(--color-text-secondary)] max-w-sm">
          {error.message || "Could not load repository data. The backend may be unavailable."}
        </p>
      </div>
      <div className="flex items-center gap-2">
        <Button onClick={reset} size="sm" variant="secondary" className="gap-2">
          <RefreshCw className="h-3.5 w-3.5" />
          Retry
        </Button>
        <Button asChild size="sm" variant="ghost" className="gap-2">
          <Link href="/">
            <ArrowLeft className="h-3.5 w-3.5" />
            Dashboard
          </Link>
        </Button>
      </div>
    </div>
  );
}
