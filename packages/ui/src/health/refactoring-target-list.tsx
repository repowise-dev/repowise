import { RefactoringCard, type RefactoringTarget, type FindingStatus } from "./refactoring-card";

export interface RefactoringTargetListProps {
  targets: RefactoringTarget[];
  onSelect?: ((target: RefactoringTarget) => void) | undefined;
  onStatusChange?: ((findingId: string, status: FindingStatus) => void) | undefined;
  emptyMessage?: string;
}

export function RefactoringTargetList({
  targets,
  onSelect,
  onStatusChange,
  emptyMessage = "No refactoring targets match the current filters.",
}: RefactoringTargetListProps) {
  if (targets.length === 0) {
    return (
      <div className="rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-surface)] p-6 text-sm text-[var(--color-text-secondary)]">
        {emptyMessage}
      </div>
    );
  }
  return (
    <div className="grid gap-3">
      {targets.map((t) => (
        <RefactoringCard
          key={t.file_path}
          target={t}
          onSelect={onSelect}
          onStatusChange={onStatusChange}
        />
      ))}
    </div>
  );
}

export type { RefactoringTarget, FindingStatus } from "./refactoring-card";
