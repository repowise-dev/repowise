import { AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "@repowise-dev/ui/ui/button";

interface ApiErrorProps {
  title?: string;
  message?: string;
  onRetry?: () => void;
}

export function ApiError({
  title = "Failed to load",
  message = "An error occurred while fetching data.",
  onRetry,
}: ApiErrorProps) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-red-500/10">
        <AlertTriangle className="h-5 w-5 text-red-500" />
      </div>
      <div>
        <p className="text-sm font-medium text-[var(--color-text-primary)]">{title}</p>
        <p className="mt-0.5 text-xs text-[var(--color-text-tertiary)] max-w-xs">{message}</p>
      </div>
      {onRetry && (
        <Button onClick={onRetry} size="sm" variant="secondary" className="gap-1.5 mt-1">
          <RefreshCw className="h-3.5 w-3.5" />
          Retry
        </Button>
      )}
    </div>
  );
}
