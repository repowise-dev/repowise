interface TestGapsListProps {
  gaps: string[];
}

export function TestGapsList({ gaps }: TestGapsListProps) {
  return (
    <ul className="space-y-1">
      {gaps.map((g) => (
        <li
          key={g}
          className="text-xs font-mono text-[var(--color-text-secondary)] break-all"
        >
          {g}
        </li>
      ))}
    </ul>
  );
}
