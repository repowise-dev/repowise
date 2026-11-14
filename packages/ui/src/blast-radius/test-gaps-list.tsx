import { FilePath } from "../shared/file-path";

interface TestGapsListProps {
  gaps: string[];
}

export function TestGapsList({ gaps }: TestGapsListProps) {
  return (
    <ul className="space-y-1">
      {gaps.map((g) => (
        <li key={g} className="flex min-w-0 text-[var(--color-text-secondary)]">
          <FilePath path={g} copyable />
        </li>
      ))}
    </ul>
  );
}
