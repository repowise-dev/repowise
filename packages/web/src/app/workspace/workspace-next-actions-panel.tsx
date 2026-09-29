"use client";

import Link from "next/link";
import type { WorkspaceActionsResponse } from "@repowise-dev/types/actions";
import { actionHref } from "@repowise-dev/ui/overview";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { WorkspaceNextActions } from "@repowise-dev/ui/workspace/workspace-next-actions";

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
    />
  );
}
