"use client";

import Link from "next/link";
import type { NextAction, WorkspaceActionsResponse } from "@repowise-dev/types/actions";
import type { AgentPromptFlavor } from "@repowise-dev/types/agent-prompts";
import { actionHref } from "@repowise-dev/ui/overview";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { WorkspaceNextActions } from "@repowise-dev/ui/workspace/workspace-next-actions";
import { getActionPrompt } from "@/lib/api/actions";

async function loadPrompt(repoId: string, action: NextAction, flavor: AgentPromptFlavor) {
  return (await getActionPrompt(repoId, action.id, { flavor })).text;
}

/** The local app's routes for the workspace "Do next"; the list is shared. */
export function WorkspaceNextActionsPanel({ data }: { data: WorkspaceActionsResponse }) {
  return (
    <WorkspaceNextActions
      data={data}
      LinkComponent={Link}
      hrefFor={(repoId, action) => actionHref(action, `/repos/${repoId}`)}
      repoHref={(repoId) => `/repos/${repoId}/overview`}
      fileHref={(repoId, path) => fileEntityPath(`/repos/${repoId}`, path)}
      contractsHref="/workspace/contracts"
      loadPrompt={loadPrompt}
    />
  );
}
