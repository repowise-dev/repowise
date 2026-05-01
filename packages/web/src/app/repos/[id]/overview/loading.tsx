import { Skeleton } from "@repowise-dev/ui/ui/skeleton";
import { Card, CardContent } from "@repowise-dev/ui/ui/card";

export default function OverviewLoading() {
  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1600px]">
      {/* Hero */}
      <div className="flex flex-col sm:flex-row items-start gap-6">
        <Skeleton className="h-[160px] w-[160px] rounded-full shrink-0" />
        <div className="flex-1 space-y-3 w-full">
          <div className="space-y-2">
            <Skeleton className="h-6 w-48" />
            <Skeleton className="h-3 w-72" />
          </div>
          <div className="flex gap-2">
            <Skeleton className="h-8 w-20" />
            <Skeleton className="h-8 w-28" />
            <Skeleton className="h-8 w-32" />
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-5">
            {Array.from({ length: 5 }).map((_, i) => (
              <Card key={i}>
                <CardContent className="p-3 space-y-2">
                  <Skeleton className="h-3 w-12" />
                  <Skeleton className="h-6 w-16" />
                </CardContent>
              </Card>
            ))}
          </div>
        </div>
      </div>

      {/* Grid */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <Card>
            <CardContent className="p-6 space-y-3">
              <Skeleton className="h-4 w-36" />
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-8 w-full" />
              ))}
            </CardContent>
          </Card>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Card>
              <CardContent className="p-6 space-y-2">
                <Skeleton className="h-4 w-28" />
                {Array.from({ length: 5 }).map((_, i) => (
                  <Skeleton key={i} className="h-5 w-full" />
                ))}
              </CardContent>
            </Card>
            <Card>
              <CardContent className="p-6 space-y-2">
                <Skeleton className="h-4 w-32" />
                {Array.from({ length: 5 }).map((_, i) => (
                  <Skeleton key={i} className="h-5 w-full" />
                ))}
              </CardContent>
            </Card>
          </div>
        </div>
        <div className="space-y-4">
          <Card>
            <CardContent className="p-6">
              <Skeleton className="h-[120px] w-[120px] rounded-full mx-auto" />
            </CardContent>
          </Card>
          <Card>
            <CardContent className="p-6">
              <Skeleton className="h-[220px] w-full" />
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}
