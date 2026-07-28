"use client";

/**
 * Download this repository's architecture as a Structurizr DSL model.
 *
 * The file is a model *fragment* by default — the same artefact
 * `repowise export --format structurizr` writes — so it drops into a
 * workspace the user already owns without clobbering their views or styles.
 * It carries its own header comment explaining that and showing the
 * `!include` snippet, which is what stops someone who never saw the terminal
 * output from reading it as broken.
 *
 * Note the model is grouped by container and component (package manifests and
 * directories), not by the layers this page's canvas draws; layer membership
 * rides along as a tag on every element. Hence the neutral label — this is not
 * "export what I am looking at".
 */

import { useCallback, useRef, useState } from "react";
import { FileType2, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { getStructurizrDsl } from "@repowise-dev/api-client/c4";
import { downloadTextFile } from "@/lib/utils/download";

const FILENAME = "repowise-model.dsl";

export function ZoomExportButton({
  repoId,
  disabled,
}: {
  repoId: string;
  disabled?: boolean;
}) {
  const [working, setWorking] = useState(false);
  // `working` is only true after a re-render, and `disabled` follows it, so two
  // clicks inside one tick both get through and both fetch. A ref is read and
  // set synchronously, which is the only thing that closes that window.
  const inFlight = useRef(false);

  const onClick = useCallback(async () => {
    if (inFlight.current) return;
    inFlight.current = true;
    setWorking(true);
    try {
      const dsl = await getStructurizrDsl(repoId);
      downloadTextFile(dsl, FILENAME, "text/plain");
      toast.success(`Downloaded ${FILENAME}`);
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
  }, [repoId]);

  return (
    <button
      type="button"
      onClick={() => void onClick()}
      disabled={disabled || working}
      aria-busy={working}
      title="Download this repository's architecture as a Structurizr DSL model fragment"
      className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--color-border-default)] px-2.5 py-1.5 text-xs font-medium text-[var(--color-text-secondary)] transition-colors hover:text-[var(--color-text-primary)] disabled:cursor-not-allowed disabled:opacity-50"
    >
      {working ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" />
      ) : (
        <FileType2 className="h-3.5 w-3.5" />
      )}
      {/* The label carries the state, not just the icon: lucide marks its
          glyphs aria-hidden, so a spinner alone tells a screen-reader user
          nothing happened. */}
      {working ? "Building…" : "Structurizr DSL"}
    </button>
  );
}
