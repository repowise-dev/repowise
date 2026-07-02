/**
 * Presentational chrome for the Health dashboard: section heading, the loading
 * skeleton that mirrors the real layout, and the error panel. All token-driven
 * so both editor themes render correctly via the `.dark` class on the root.
 */

/** A section title with a muted supporting line. */
export function SectionHeading({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div className="min-w-0">
      <h2 className="text-sm font-semibold uppercase tracking-wider text-[var(--color-text-tertiary)]">
        {title}
      </h2>
      {subtitle ? (
        <p className="mt-0.5 truncate text-xs text-[var(--color-text-tertiary)]">{subtitle}</p>
      ) : null}
    </div>
  );
}

/** A pulsing placeholder block. */
function Block({ className }: { className: string }) {
  return (
    <div
      className={`animate-pulse rounded-xl border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] ${className}`}
    />
  );
}

/** Skeleton laid out like the loaded dashboard so the transition is calm. */
export function DashboardSkeleton() {
  return (
    <div className="mx-auto max-w-[1400px] space-y-8 px-6 py-6" aria-busy="true" aria-label="Loading health">
      <div className="space-y-2">
        <Block className="h-6 w-40 border-0" />
        <Block className="h-4 w-64 border-0" />
      </div>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        <Block className="h-32" />
        <Block className="h-32" />
        <Block className="h-32" />
      </div>
      <Block className="h-10" />
      <Block className="h-[640px]" />
    </div>
  );
}

/** Error panel shown when the host could not serve the health payloads. */
export function DashboardError({ message }: { message: string }) {
  return (
    <div className="mx-auto max-w-[1400px] px-6 py-6">
      <div className="rounded-xl border border-[var(--color-error)] bg-[var(--color-bg-surface)] p-6">
        <h2 className="text-sm font-semibold text-[var(--color-error)]">
          Health data is unavailable
        </h2>
        <p className="mt-2 text-sm text-[var(--color-text-secondary)]">{message}</p>
        <p className="mt-3 text-xs text-[var(--color-text-tertiary)]">
          Make sure the local Repowise server is running and this repository is indexed.
        </p>
      </div>
    </div>
  );
}
