import type { ReactNode } from "react";
import { cn } from "../lib/cn";

export function Th({ children }: { children: ReactNode }) {
  return (
    <th className="text-left text-xs font-medium text-[var(--color-text-tertiary)] py-1.5 pr-4 whitespace-nowrap">
      {children}
    </th>
  );
}

export function Td({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return (
    <td
      className={cn(
        "text-xs text-[var(--color-text-secondary)] py-1.5 pr-4 align-top",
        className,
      )}
    >
      {children}
    </td>
  );
}
