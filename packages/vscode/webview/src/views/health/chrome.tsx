/**
 * Presentational chrome for the Health dashboard: the loading skeleton that
 * mirrors the real layout, and the error panel. Token-driven so both editor
 * themes render correctly via the `.dark` class on the root.
 */

/** A pulsing placeholder block. */
function Block({ className }: { className: string }) {
  return <div className={`animate-pulse rounded-lg bg-[var(--color-bg-inset)] ${className}`} />;
}

/** Skeleton laid out like the loaded dashboard so content landing does not
 *  reflow the panel: header, lede (figure beside prose, then the stat ribbon),
 *  the map section, and the trend section. */
export function DashboardSkeleton() {
  return (
    <div
      className="mx-auto flex max-w-[1400px] flex-col gap-6 px-6 py-6 sm:gap-8"
      aria-busy="true"
      aria-label="Loading health"
    >
      <div className="flex flex-col gap-2">
        <Block className="h-7 w-40" />
        <Block className="h-5 w-64" />
      </div>

      <div className="flex flex-col gap-6">
        <div className="grid grid-cols-1 gap-6 xl:grid-cols-[240px_minmax(0,1fr)]">
          <div className="flex flex-col gap-3">
            <Block className="h-14 w-32" />
            <Block className="h-2 w-full" />
          </div>
          <div className="flex flex-col gap-2">
            <Block className="h-4 w-full" />
            <Block className="h-4 w-11/12" />
            <Block className="h-4 w-4/5" />
          </div>
        </div>
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
          {Array.from({ length: 5 }).map((_, i) => (
            <Block key={i} className="h-12" />
          ))}
        </div>
      </div>

      <div className="flex flex-col gap-3 border-t border-[var(--color-border-default)] pt-6 sm:pt-8">
        <div className="flex items-baseline justify-between gap-4">
          <Block className="h-5 w-40" />
          <Block className="h-7 w-64" />
        </div>
        <Block className="h-[640px] w-full" />
        <Block className="h-4 w-72" />
      </div>

      <div className="flex flex-col gap-3 border-t border-[var(--color-border-default)] pt-6 sm:pt-8">
        <Block className="h-5 w-24" />
        <Block className="h-64 w-full" />
      </div>
    </div>
  );
}

/** Error panel shown when the host could not serve the health payloads. */
export function DashboardError({ message }: { message: string }) {
  return (
    <div className="mx-auto max-w-[1400px] px-6 py-6">
      <div className="rounded-xl border border-[var(--color-error)] bg-[var(--color-bg-surface)] p-6">
        <h2 className="text-[15px] font-semibold text-[var(--color-error)]">
          Health data is unavailable
        </h2>
        <p className="mt-2 text-[15px] text-[var(--color-text-secondary)]">{message}</p>
        <p className="mt-3 text-xs text-[var(--color-text-tertiary)]">
          Make sure the local Repowise server is running and this repository is indexed.
        </p>
      </div>
    </div>
  );
}
