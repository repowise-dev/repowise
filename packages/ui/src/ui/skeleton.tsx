import { cn } from "../lib/cn";

/**
 * A still placeholder box. It does NOT animate on its own: motion belongs to
 * the pending region, so a page full of skeletons reads as one wait rather
 * than as N independent ones. Wrap the region in `SkeletonRegion` to add the
 * sweep; a bare `Skeleton` is a correct, calm silhouette.
 *
 * Fill is always `--color-bg-elevated`. A placeholder on any other plane
 * makes two adjacent loading states look like two different things.
 */
function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      data-skeleton=""
      className={cn("rounded-md bg-[var(--color-bg-elevated)]", className)}
      {...props}
    />
  );
}

export interface SkeletonRegionProps
  extends React.HTMLAttributes<HTMLDivElement> {
  /**
   * Announced to assistive tech in place of the silhouette. Say what is
   * loading — "Loading performance opportunities" beats "Loading".
   */
  label?: string;
}

/**
 * Marks a block of the page as pending and runs one slow sweep across every
 * `Skeleton` inside it (see `.skeleton-region` in the shared stylesheet).
 * Reduced motion leaves the still silhouette.
 *
 * Wrap the region that is waiting, not each box. Nesting two regions is not
 * wrong, but it buys nothing: the sweep is viewport-anchored either way.
 */
function SkeletonRegion({
  className,
  children,
  label = "Loading…",
  ...props
}: SkeletonRegionProps) {
  return (
    <div aria-busy="true" className={cn("skeleton-region", className)} {...props}>
      {children}
      <span className="sr-only" role="status">
        {label}
      </span>
    </div>
  );
}

export { Skeleton, SkeletonRegion };
