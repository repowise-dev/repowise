import { Skeleton } from "@/components/ui/skeleton";

export default function CoverageLoading() {
  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1600px]">
      <div>
        <Skeleton className="h-7 w-36" />
        <Skeleton className="h-4 w-52 mt-1.5" />
      </div>
      <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
        <div className="flex items-center justify-center">
          <Skeleton className="h-44 w-44 rounded-full" />
        </div>
        <div className="md:col-span-2 grid grid-cols-3 gap-3 content-center">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-20 rounded-lg" />
          ))}
        </div>
      </div>
      <Skeleton className="h-[350px] rounded-lg" />
    </div>
  );
}
