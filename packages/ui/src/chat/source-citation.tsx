"use client";

import { FileCode } from "lucide-react";
import type { ChatCitation } from "@repowise-dev/types/chat";

/**
 * Singular citation chip for the typed `ChatCitation` shape (file path +
 * optional symbol + optional line range). Distinct from `SourceCitations`,
 * which derives chips from raw tool result blobs — this one renders an
 * already-typed citation reference.
 *
 * The component is framework-neutral: pass `buildHref` to control routing.
 * If omitted, the chip renders as plain text with no link wrapping.
 */
export interface SourceCitationProps {
  citation: ChatCitation;
  buildHref?: (citation: ChatCitation) => string;
  /** Optional 1-based numeric badge prefix. */
  index?: number;
  className?: string;
}

export function SourceCitation({
  citation,
  buildHref,
  index,
  className,
}: SourceCitationProps) {
  const href = buildHref ? buildHref(citation) : null;
  const label = formatCitationLabel(citation);

  const inner = (
    <>
      {typeof index === "number" && (
        <span className="flex items-center justify-center h-3.5 w-3.5 rounded-sm bg-[var(--color-bg-overlay)] text-[9px] font-bold text-[var(--color-text-tertiary)] shrink-0">
          {index}
        </span>
      )}
      <FileCode className="h-3 w-3 shrink-0 opacity-60" />
      <span className="truncate max-w-[220px] font-mono">{label}</span>
      {citation.start_line != null && (
        <span className="text-[9px] tabular-nums text-[var(--color-text-tertiary)] shrink-0">
          L{citation.start_line}
          {citation.end_line != null && citation.end_line !== citation.start_line
            ? `-${citation.end_line}`
            : ""}
        </span>
      )}
    </>
  );

  const baseCls =
    "group inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] px-2 py-1 text-[10px] text-[var(--color-text-secondary)]";
  const interactiveCls =
    "hover:border-[var(--color-accent-primary)] hover:text-[var(--color-accent-primary)] hover:bg-[var(--color-accent-muted)] transition-all";

  if (href) {
    return (
      <a
        href={href}
        className={[baseCls, interactiveCls, className ?? ""].join(" ")}
      >
        {inner}
      </a>
    );
  }
  return (
    <span className={[baseCls, className ?? ""].join(" ")}>{inner}</span>
  );
}

function formatCitationLabel(c: ChatCitation): string {
  const filename = c.file_path.split("/").pop() ?? c.file_path;
  return c.symbol_name ? `${filename}::${c.symbol_name}` : filename;
}
