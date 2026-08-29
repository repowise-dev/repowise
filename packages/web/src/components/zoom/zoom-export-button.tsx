"use client";

/**
 * Download this repository's architecture as Structurizr DSL.
 *
 * Two shapes, because the two audiences want opposite files:
 *
 * - **Workspace** (the default). A complete `workspace { … }` with default
 *   views and styles. Someone downloading through a browser almost by
 *   definition has no Structurizr workspace yet — if they had one they would
 *   be running `repowise export` inside their repo. This file opens as-is in
 *   Structurizr Lite or pasted into structurizr.com.
 * - **Model fragment**. Just the `model { … }` body, for the smaller group who
 *   already keep a hand-written workspace.dsl and want the model regenerated
 *   under their own views. Useless on its own — the parser rejects a file that
 *   does not start with `workspace`, and `!include` only resolves from disk —
 *   so it is the secondary item, labelled with what it needs.
 *
 * Note the model is grouped by container and component (package manifests and
 * directories), not by the layers this page's canvas draws; layer membership
 * rides along as a tag on every element. Hence the neutral label — this is not
 * "export what I am looking at".
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { FileType2, ChevronDown } from "lucide-react";
import { Spinner } from "@repowise-dev/ui/ui/spinner";
import { toast } from "sonner";
import { getStructurizrDsl } from "@repowise-dev/api-client/c4";
import { downloadTextFile } from "@/lib/utils/download";

const WORKSPACE_FILENAME = "workspace.dsl";
const FRAGMENT_FILENAME = "repowise-model.dsl";

export function ZoomExportButton({
  repoId,
  disabled,
}: {
  repoId: string;
  disabled?: boolean;
}) {
  const [working, setWorking] = useState(false);
  const [open, setOpen] = useState(false);
  // `working` is only true after a re-render, and `disabled` follows it, so two
  // clicks inside one tick both get through and both fetch. A ref is read and
  // set synchronously, which is the only thing that closes that window.
  const inFlight = useRef(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as globalThis.Node)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    window.addEventListener("mousedown", onPointer);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("mousedown", onPointer);
      window.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const download = useCallback(
    async (standalone: boolean) => {
      if (inFlight.current) return;
      inFlight.current = true;
      setWorking(true);
      setOpen(false);
      const filename = standalone ? WORKSPACE_FILENAME : FRAGMENT_FILENAME;
      try {
        const dsl = await getStructurizrDsl(repoId, { standalone });
        downloadTextFile(dsl, filename, "text/plain");
        toast.success(`Downloaded ${filename}`);
      } catch (error) {
        // The client throws with the status code, so a 404 (no such repo) and a
        // 500 (built and failed) are different problems. Keep it out of the toast
        // and in the console, where someone debugging will look.
        console.error("Structurizr DSL export failed", error);
        toast.error("Couldn't build the Structurizr DSL");
      } finally {
        inFlight.current = false;
        setWorking(false);
      }
    },
    [repoId],
  );

  return (
    // z-50 on the wrapper, not just the menu: the zoom canvas floats its
    // breadcrumb/search overlay at z-10 from a *later* DOM subtree, so at equal
    // z-index that overlay paints over this menu. Raising the whole wrapper
    // puts the menu's stacking context above the canvas chrome outright.
    <div ref={rootRef} className="relative z-50">

      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled || working}
        aria-busy={working}
        aria-haspopup="menu"
        aria-expanded={open}
        title="Download this repository's architecture as Structurizr DSL"
        className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-2.5 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-text-primary)] disabled:cursor-not-allowed disabled:opacity-50"
      >
        {working ? (
          <Spinner size="sm" />
        ) : (
          <FileType2 className="h-3.5 w-3.5" />
        )}
        {/* The label carries the state, not just the icon: lucide marks its
            glyphs aria-hidden, so a spinner alone tells a screen-reader user
            nothing happened. */}
        {working ? "Building…" : "Structurizr DSL"}
        {!working && <ChevronDown className="h-3 w-3" />}
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-[calc(100%+4px)] z-10 w-72 rounded-lg border border-[var(--color-border-default)] bg-[var(--color-bg-elevated)] p-1 shadow-lg"
        >
          <ExportOption
            label="Workspace (workspace.dsl)"
            hint="Complete file with default views. Opens as-is in Structurizr."
            onClick={() => void download(true)}
          />
          <ExportOption
            label="Model fragment (repowise-model.dsl)"
            hint="Model block only, to !include from a workspace.dsl you already have."
            onClick={() => void download(false)}
          />
        </div>
      )}
    </div>
  );
}

function ExportOption({
  label,
  hint,
  onClick,
}: {
  label: string;
  hint: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className="block w-full rounded-md px-2.5 py-2 text-left transition-colors hover:bg-[var(--color-border-subtle)]"
    >
      <span className="block text-xs font-medium text-[var(--color-text-primary)]">
        {label}
      </span>
      <span className="mt-0.5 block text-[11px] leading-snug text-[var(--color-text-secondary)]">
        {hint}
      </span>
    </button>
  );
}
