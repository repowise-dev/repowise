import * as React from "react";
import { AlertTriangle, Compass, RefreshCw } from "lucide-react";
import { cn } from "../lib/cn";
import { Button } from "../ui/button";

const FRAME =
  "flex min-h-[60vh] flex-col items-center justify-center gap-4 px-4 py-10 text-center sm:px-6";

/**
 * A rendered link element, e.g. `<Link href="/">Back to dashboard</Link>`.
 * The host passes its own router link so this package never imports a
 * framework router; it is styled as a ghost button via `Button asChild`.
 */
export type RouteLinkElement = React.ReactElement;

export interface RouteErrorProps {
  title?: string;
  /**
   * One plain sentence. Do not pass a raw `error.message`: it is written for
   * developers and can leak internals. Log the error instead.
   */
  message?: string;
  /** Server error digest, shown in mono so a user can quote it. */
  digest?: string;
  /** Label before the digest. Default "Reference". */
  digestLabel?: string;
  /** Usually the error boundary's `reset`. */
  onRetry?: () => void;
  retryLabel?: string;
  /** Optional way out, see `RouteLinkElement`. */
  back?: RouteLinkElement;
  className?: string;
}

/** Route-level error boundary body: plain sentence, digest, retry, back link. */
export function RouteError({
  title = "Something went wrong",
  message = "This page could not be loaded. Try again, or come back in a moment.",
  digest,
  digestLabel = "Reference",
  onRetry,
  retryLabel = "Try again",
  back,
  className,
}: RouteErrorProps) {
  return (
    <div role="alert" className={cn(FRAME, className)}>
      <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[var(--color-error-muted)]">
        <AlertTriangle className="h-5 w-5 text-[var(--color-error)]" aria-hidden />
      </div>
      <div className="max-w-[52ch] space-y-1">
        <h1 className="text-[18px] font-semibold text-[var(--color-text-primary)]">{title}</h1>
        <p className="text-xs leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
          {message}
        </p>
        {digest && (
          <p className="break-all font-mono text-[10px] text-[var(--color-text-tertiary)]">
            {digestLabel}: {digest}
          </p>
        )}
      </div>
      {(onRetry || back) && (
        <div className="flex flex-wrap items-center justify-center gap-2">
          {onRetry && (
            <Button onClick={onRetry} size="sm" variant="secondary" className="h-8 gap-1.5">
              <RefreshCw className="h-3.5 w-3.5" aria-hidden />
              {retryLabel}
            </Button>
          )}
          {back && (
            <Button asChild size="sm" variant="ghost" className="h-8">
              {back}
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

export interface RouteNotFoundProps {
  title?: string;
  description?: string;
  /** Bare icon; tinted tertiary. Defaults to a compass. */
  icon?: React.ReactNode;
  /** Way back, see `RouteLinkElement`. */
  back?: RouteLinkElement;
  className?: string;
}

/** Route-level 404 body. Neutral, never an error colour. */
export function RouteNotFound({
  title = "Page not found",
  description = "The page you are looking for does not exist or has moved.",
  icon,
  back,
  className,
}: RouteNotFoundProps) {
  return (
    <div className={cn(FRAME, className)}>
      <span
        aria-hidden
        className="inline-flex text-[var(--color-text-tertiary)] [&_svg]:h-6 [&_svg]:w-6"
      >
        {icon ?? <Compass />}
      </span>
      <div className="max-w-[52ch] space-y-1">
        <h1 className="text-[18px] font-semibold text-[var(--color-text-primary)]">{title}</h1>
        <p className="text-xs leading-relaxed text-[var(--color-text-secondary)] [text-wrap:pretty]">
          {description}
        </p>
      </div>
      {back && (
        <Button asChild size="sm" variant="outline" className="h-8">
          {back}
        </Button>
      )}
    </div>
  );
}
