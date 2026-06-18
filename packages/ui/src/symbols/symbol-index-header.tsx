import { Code2 } from "lucide-react";

/**
 * The Symbol Index page header. Lifted out of the web architecture route so
 * the title + descriptive copy live with the symbol surface and propagate on
 * a package bump (the index header previously couldn't drift-free track the
 * drawer surface it sits above).
 */
export function SymbolIndexHeader() {
  return (
    <div>
      <h1 className="mb-1 flex items-center gap-2 text-xl font-semibold text-[var(--color-text-primary)]">
        <Code2 className="h-5 w-5 text-[var(--color-accent-primary)]" />
        Symbol Index
      </h1>
      <p className="text-sm text-[var(--color-text-secondary)]">
        Searchable index of all functions, classes, and exports — sorted by
        importance, with hotspot, entry-point, and complexity signals inline.
      </p>
    </div>
  );
}
