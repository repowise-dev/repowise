import { cn } from "../lib/cn";

function Skeleton({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "animate-pulse rounded-md bg-[var(--color-bg-elevated)]",
        className,
      )}
      {...props}
    />
  );
}

export { Skeleton };
