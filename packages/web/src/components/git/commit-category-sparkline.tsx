import {
  Tooltip,
  TooltipTrigger,
  TooltipContent,
} from "@/components/ui/tooltip";

interface CommitCategorySparklineProps {
  categories: Record<string, number>;
}

const CATEGORY_CONFIG: Record<string, { color: string; label: string }> = {
  feature: { color: "#5b9cf6", label: "Feature" },
  fix: { color: "#ef4444", label: "Fix" },
  refactor: { color: "#a855f7", label: "Refactor" },
  dependency: { color: "#f59520", label: "Dependency" },
};

const CATEGORY_ORDER = ["feature", "fix", "refactor", "dependency"];

export function CommitCategorySparkline({ categories }: CommitCategorySparklineProps) {
  const total = CATEGORY_ORDER.reduce((sum, k) => sum + (categories[k] || 0), 0);
  if (total === 0) return null;

  return (
    <div className="flex h-2 w-full rounded-full overflow-hidden bg-[var(--color-bg-elevated)]">
      {CATEGORY_ORDER.map((key) => {
        const count = categories[key] || 0;
        if (count === 0) return null;
        const config = CATEGORY_CONFIG[key];
        return (
          <Tooltip key={key}>
            <TooltipTrigger asChild>
              <div
                className="h-full transition-all"
                style={{
                  flex: count,
                  backgroundColor: config.color,
                }}
              />
            </TooltipTrigger>
            <TooltipContent>
              <span className="text-xs">
                {config.label}: {count} ({Math.round((count / total) * 100)}%)
              </span>
            </TooltipContent>
          </Tooltip>
        );
      })}
    </div>
  );
}
