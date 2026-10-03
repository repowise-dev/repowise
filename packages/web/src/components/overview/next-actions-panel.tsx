"use client";

import Link from "next/link";
import { useCallback } from "react";
import type { ActionsResponse, NextAction } from "@repowise-dev/types/actions";
import type { AgentPromptFlavor } from "@repowise-dev/types/agent-prompts";
import { NextActions } from "@repowise-dev/ui/overview/next-actions";
import { actionHref } from "@repowise-dev/ui/overview";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { getActionPrompt, setActionState } from "@/lib/api/actions";

/**
 * The local app's binding for "Do next": its routes and its write path. The
 * list itself, and every word on it, is the shared component.
 */
export function NextActionsPanel({ repoId, data }: { repoId: string; data: ActionsResponse }) {
  const base = `/repos/${repoId}`;
  const loadPrompt = useCallback(
    async (action: NextAction, flavor: AgentPromptFlavor) =>
      (await getActionPrompt(repoId, action.id, { flavor })).text,
    [repoId],
  );
  return (
    <NextActions
      data={data}
      loadPrompt={loadPrompt}
      LinkComponent={Link}
      hrefFor={(action) => actionHref(action, base)}
      fileHref={(path) => fileEntityPath(base, path)}
      onSetState={async (action, state) => {
        await setActionState(repoId, action.id, {
          state,
          fingerprint: action.fingerprint,
        });
      }}
    />
  );
}
