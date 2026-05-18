import { RefactoringCard, type RefactoringTarget } from "./refactoring-card.js";

export interface RefactoringTargetListProps {
  targets: RefactoringTarget[];
  onSelect?: (target: RefactoringTarget) => void;
  emptyMessage?: string;
}

export function RefactoringTargetList({
  targets,
  onSelect,
  emptyMessage = "No refactoring targets — your health findings list is empty.",
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
        <RefactoringCard key={t.file_path} target={t} onSelect={onSelect} />
      ))}
    </div>
  );
}

export type { RefactoringTarget } from "./refactoring-card.js";
