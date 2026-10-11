import { ProportionBar } from "../shared/proportion-bar";

interface CommitCategorySparklineProps {
  categories: Record<string, number>;
}

const CATEGORY_LABEL: Record<string, string> = {
  feature: "Feature",
  fix: "Fix",
  refactor: "Refactor",
  dependency: "Dependency",
};

/** A file's commits by type, on the shared share bar. */
export function CommitCategorySparkline({ categories }: CommitCategorySparklineProps) {
  return (
    <ProportionBar
      label="Commits by type"
      size="sm"
      segments={Object.entries(CATEGORY_LABEL).map(([key, label]) => ({
        key,
        label,
        value: categories[key] || 0,
      }))}
    />
  );
}
