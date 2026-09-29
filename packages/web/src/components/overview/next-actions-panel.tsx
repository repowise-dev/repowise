"use client";

import Link from "next/link";
import type { ActionsResponse } from "@repowise-dev/types/actions";
import { NextActions } from "@repowise-dev/ui/overview/next-actions";
import { actionHref } from "@repowise-dev/ui/overview";
import { fileEntityPath } from "@repowise-dev/ui/shared/entity";
import { setActionState } from "@/lib/api/actions";

/**
 * The local app's binding for "Do next": its routes and its write path. The
 * list itself, and every word on it, is the shared component.
 */
export function NextActionsPanel({
  repoId,
  repoName,
  data,
}: {
  repoId: string;
  repoName: string;
  data: ActionsResponse;
}) {
  const base = `/repos/${repoId}`;
  return (
    <NextActions
      data={data}
      repoName={repoName}
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
