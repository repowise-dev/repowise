import { Skeleton } from "@repowise-dev/ui/ui/skeleton";

export default function GraphLoading() {
  return (
    <div className="flex flex-col h-screen">
      <div className="shrink-0 px-4 sm:px-6 py-4 border-b border-[var(--color-border-default)]">
        <Skeleton className="h-7 w-48" />
        <Skeleton className="h-4 w-80 mt-1.5" />
      </div>
      <div className="flex-1 p-4">
        <Skeleton className="h-full w-full rounded-lg" />
      </div>
    </div>
  );
}
