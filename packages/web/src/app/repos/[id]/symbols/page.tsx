import type { Metadata } from "next";
import { Code2 } from "lucide-react";
import { SymbolTable } from "@/components/symbols/symbol-table";

export const metadata: Metadata = { title: "Symbols" };

export default async function SymbolsPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;

  return (
    <div className="p-4 sm:p-6 space-y-6 max-w-[1600px]">
      <div>
        <h1 className="text-xl font-semibold text-[var(--color-text-primary)] mb-1 flex items-center gap-2">
          <Code2 className="h-5 w-5 text-[var(--color-accent-primary)]" />
          Symbol Index
        </h1>
        <p className="text-sm text-[var(--color-text-secondary)]">
          Searchable index of all functions, classes, and exports.
        </p>
      </div>
      <SymbolTable repoId={id} />
    </div>
  );
}
