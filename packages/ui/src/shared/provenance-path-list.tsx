import { ArrowRight } from "lucide-react";

export interface ProvenancePath {
  nodes: string[];
  provenance: string;
  filePath?: string | undefined;
  line?: number | null | undefined;
}

export interface ProvenancePathListProps {
  paths: ProvenancePath[];
  total?: number | undefined;
  fileHref?: ((path: string, line?: number | null) => string | undefined) | undefined;
  symbolHref?: ((symbolId: string) => string | undefined) | undefined;
}

const PROVENANCE_LABEL: Record<string, string> = {
  "call-site": "Exact call-site resolution",
  direct: "Direct local resolution",
  "reliable-edge": "Reliable resolved edge",
  "name-fallback": "Compatibility name fallback",
};

function nodeHref(
  node: string,
  fileHref: ProvenancePathListProps["fileHref"],
  symbolHref: ProvenancePathListProps["symbolHref"],
): string | undefined {
  const symbol = symbolHref?.(node);
  if (symbol) return symbol;
  const separator = node.indexOf("::");
  const file = separator >= 0 ? node.slice(0, separator) : node;
  return /[./\\]/.test(file) ? fileHref?.(file, null) : undefined;
}

/** Shared caller-to-sink rendering for Code Health and structured plans. */
export function ProvenancePathList({
  paths,
  total = paths.length,
  fileHref,
  symbolHref,
}: ProvenancePathListProps) {
  if (paths.length === 0) {
    return (
      <p className="text-sm text-[var(--color-text-tertiary)]">No resolved path was recorded.</p>
    );
  }
  return (
    <div>
      <div className="divide-y divide-[var(--color-border-default)] border-y border-[var(--color-border-default)]">
        {paths.map((path, index) => (
          <div key={`${path.nodes.join("→")}:${index}`} className="py-3">
            <div className="flex flex-wrap items-center gap-1.5">
              {path.nodes.map((node, nodeIndex) => {
                const href = nodeHref(node, fileHref, symbolHref);
                const name = node.split("::").pop() ?? node;
                return (
                  <span
                    key={`${node}:${nodeIndex}`}
                    className="inline-flex min-w-0 items-center gap-1.5"
                  >
                    {nodeIndex > 0 ? (
                      <ArrowRight
                        className="h-3 w-3 shrink-0 text-[var(--color-text-tertiary)]"
                        aria-hidden
                      />
                    ) : null}
                    {href ? (
                      <a
                        href={href}
                        title={node}
                        className="break-all font-mono text-xs text-[var(--color-text-secondary)] underline-offset-2 hover:text-[var(--color-accent-primary)] hover:underline"
                      >
                        {name}
                      </a>
                    ) : (
                      <span
                        title={node}
                        className="break-all font-mono text-xs text-[var(--color-text-secondary)]"
                      >
                        {name}
                      </span>
                    )}
                  </span>
                );
              })}
            </div>
            <p className="mt-1.5 font-mono text-[10px] uppercase tracking-[0.12em] text-[var(--color-text-tertiary)]">
              {PROVENANCE_LABEL[path.provenance] ?? path.provenance}
            </p>
          </div>
        ))}
      </div>
      {total > paths.length ? (
        <p className="mt-2 text-xs text-[var(--color-text-tertiary)]">
          {paths.length.toLocaleString()} paths shown of {total.toLocaleString()} recorded
          observations.
        </p>
      ) : null}
    </div>
  );
}
