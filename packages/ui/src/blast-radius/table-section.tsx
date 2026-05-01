import type { ReactNode } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";

interface TableSectionProps {
  title: string;
  empty: boolean;
  children: ReactNode;
  /** Optional empty-state copy. Defaults to "None". */
  emptyLabel?: string;
}

export function TableSection({
  title,
  empty,
  children,
  emptyLabel = "None",
}: TableSectionProps) {
  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="text-sm">{title}</CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        {empty ? (
          <p className="text-xs text-[var(--color-text-tertiary)] py-2">{emptyLabel}</p>
        ) : (
          children
        )}
      </CardContent>
    </Card>
  );
}
