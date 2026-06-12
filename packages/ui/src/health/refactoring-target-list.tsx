import { RefactoringCard, type RefactoringTarget, type FindingStatus } from "./refactoring-card";

export interface RefactoringTargetListProps {
  targets: RefactoringTarget[];
  onSelect?: ((target: RefactoringTarget) => void) | undefined;
  onStatusChange?: ((findingId: string, status: FindingStatus) => void) | undefined;
  onGeneratePrompt?: ((target: RefactoringTarget) => void) | undefined;
  emptyMessage?: string;
  /** File path of the card to flash-highlight (quadrant click). */
  highlightedPath?: string | null | undefined;
}

export function RefactoringTargetList({
  targets,
  onSelect,
  onStatusChange,
  onGeneratePrompt,
  emptyMessage = "No refactoring targets match the current filters.",
  highlightedPath,
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
          onGeneratePrompt={onGeneratePrompt}
          highlighted={highlightedPath === t.file_path}
        />
      ))}
    </div>
  );
}

export type { RefactoringTarget, FindingStatus } from "./refactoring-card";
