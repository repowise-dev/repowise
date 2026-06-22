"use client";

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@repowise-dev/ui/ui/dialog";
import { useChangelog } from "@/lib/hooks/use-meta-version";

export interface WhatsNewModalProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Renders repowise's recent release notes from the bundled changelog. */
export function WhatsNewModal({ open, onOpenChange }: WhatsNewModalProps) {
  // Only fetch once the modal is opened.
  const { entries, error, isLoading } = useChangelog(open);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[80vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>What&apos;s new in repowise</DialogTitle>
        </DialogHeader>

        {isLoading && (
          <p className="text-sm text-[var(--color-text-tertiary)]">Loading release notes...</p>
        )}
        {error && (
          <p className="text-sm text-[var(--color-text-tertiary)]">
            Release notes unavailable.{" "}
            <a
              href="https://github.com/repowise-dev/repowise/releases"
              target="_blank"
              rel="noreferrer"
              className="text-[var(--color-accent-primary)] underline"
            >
              View on GitHub
            </a>
          </p>
        )}

        <div className="space-y-6">
          {entries.map((entry) => (
            <section key={entry.version} className="space-y-2">
              <h3 className="flex items-baseline gap-2">
                <span className="text-base font-semibold text-[var(--color-text-primary)]">
                  v{entry.version}
                </span>
                {entry.label && (
                  <span className="text-xs text-[var(--color-text-tertiary)]">{entry.label}</span>
                )}
              </h3>
              {entry.sections.map((section) => (
                <div key={section.name} className="space-y-1">
                  <p className="text-xs font-medium uppercase tracking-wide text-[var(--color-text-secondary)]">
                    {section.name}
                  </p>
                  <ul className="list-disc space-y-1 pl-5 text-sm text-[var(--color-text-secondary)]">
                    {section.items.map((item, i) => (
                      <li key={i}>{stripMarkdown(item)}</li>
                    ))}
                  </ul>
                </div>
              ))}
            </section>
          ))}
        </div>
      </DialogContent>
    </Dialog>
  );
}

/** Light touch: drop bold markers so they don't render as literal asterisks. */
function stripMarkdown(text: string): string {
  return text.replace(/\*\*/g, "");
}
