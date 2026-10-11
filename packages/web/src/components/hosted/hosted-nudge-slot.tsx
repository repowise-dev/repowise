"use client";

import { useEffect, useState } from "react";
import { HostedNudge } from "@repowise-dev/ui/shared/hosted-nudge";
import { config } from "@/lib/config";
import { NUDGES, hostedLink, pickNudge, type NudgeId } from "@/lib/hosted";
import { useHostedIdentity } from "@/lib/hooks/use-hosted-identity";
import { PublishItFree } from "./publish";

interface Props {
  /** Tips whose moment applies here, most specific first. A page mounts one
   *  slot, so it shows at most one tip. */
  candidates: readonly (NudgeId | false | null | undefined)[];
  /** The repo the page is about; without one the action is the CLI command. */
  repoId?: string;
  className?: string;
}

export function HostedNudgeSlot({ candidates, repoId, className }: Props) {
  const { identity } = useHostedIdentity();
  // Off until read after mount, so SSR and the first client render agree.
  const [tipsShown, setTipsShown] = useState(false);
  useEffect(() => setTipsShown(!config.getHostedTipsHidden()), []);

  const id = pickNudge(candidates, identity, tipsShown);
  if (!id) return null;
  const nudge = NUDGES[id];

  return (
    <HostedNudge
      id={id}
      text={nudge.text}
      href={hostedLink(nudge.surface, nudge.moment)}
      className={className}
      action={
        repoId ? (
          <PublishItFree repoId={repoId} />
        ) : (
          <span>
            Publish it free:{" "}
            <code className="rounded bg-[var(--color-bg-elevated)] px-1.5 py-0.5">
              repowise publish
            </code>
          </span>
        )
      }
    />
  );
}
