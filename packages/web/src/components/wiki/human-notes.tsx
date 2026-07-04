"use client";

import { toast } from "sonner";
import { HumanNotes as HumanNotesShell } from "@repowise-dev/ui/wiki/human-notes";
import { updatePageNotes } from "@/lib/api/pages";
import { toFriendlyMessage } from "@repowise-dev/ui/lib/errors";

/**
 * Web data wrapper around the presentational ``HumanNotes`` ui shell — owns the
 * persistence call and toast feedback. Notes survive regeneration server-side.
 */
export function HumanNotes({
  pageId,
  initialNotes,
}: {
  pageId: string;
  initialNotes: string | null;
}) {
  return (
    <HumanNotesShell
      initialNotes={initialNotes}
      onSave={async (value) => {
        try {
          const updated = await updatePageNotes(pageId, value);
          toast.success(updated.human_notes ? "Note saved" : "Note removed");
          return updated.human_notes;
        } catch (e) {
          toast.error("Couldn't save note", {
            description: toFriendlyMessage(e),
          });
          throw e;
        }
      }}
    />
  );
}
