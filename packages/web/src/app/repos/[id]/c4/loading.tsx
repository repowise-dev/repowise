import { Skeleton } from "@repowise-dev/ui/ui/skeleton";

export default function Loading() {
  return (
    <div className="flex flex-col h-full">
      <div className="shrink-0 px-4 sm:px-6 py-3 border-b border-[var(--color-border-default)]">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="mt-2 h-3 w-72" />
      </div>
      <div className="flex-1 p-6">
        <Skeleton className="h-full w-full rounded-lg" />
      </div>
    </div>
  );
}
