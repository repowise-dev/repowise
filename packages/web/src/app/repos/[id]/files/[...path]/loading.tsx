import { Skeleton } from "@repowise-dev/ui/ui/skeleton";

export default function FilePageLoading() {
  return (
    <div className="p-4 sm:p-6 max-w-[1200px] space-y-4">
      <Skeleton className="h-6 w-2/3" />
      <div className="flex gap-2">
        <Skeleton className="h-6 w-14" />
        <Skeleton className="h-6 w-20" />
        <Skeleton className="h-6 w-24" />
      </div>
      <Skeleton className="h-9 w-96" />
      <Skeleton className="h-96 w-full" />
    </div>
  );
}
