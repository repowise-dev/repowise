import { cn } from "../lib/cn";

/**
 * "Something is happening right now" — a stream arriving, a layout running,
 * a job in flight. Deliberately a different verb from a wait: skeletons
 * sweep, live activity breathes, on the slower 2.4s cadence `WorkingOrb`
 * already uses. Before this existed both used `animate-pulse` and a reader
 * could not tell a placeholder from a live signal.
 *
 * Decorative by default. The state it marks must also be in text nearby,
 * because under reduced motion the dot is simply still.
 *
 * Size and color are className overrides; the defaults suit a status row.
 */
export function ActivityDot({
  className,
  ...props
}: React.HTMLAttributes<HTMLSpanElement>) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "inline-block h-2 w-2 shrink-0 rounded-full bg-[var(--color-accent-primary)]",
        "motion-safe:animate-[pulse_2.4s_ease-in-out_infinite] motion-reduce:animate-none",
        className,
      )}
      {...props}
    />
  );
}
