"use client";

import * as React from "react";

import { ReleaseNotice } from "../shared/release-notice";

/**
 * The accounting-method version this build reports under.
 *
 * Hosts key the reset notice's dismissal on repository plus this value, so a
 * later methodology change announces itself once more to a reader who
 * dismissed the previous one. Bump it when the way a saving is counted
 * changes, not when a component moves.
 */
export const ACCOUNTING_METHOD_VERSION = 1;

export interface SavingsResetNoticeProps {
  /** Where "See what changed" goes. */
  methodologyHref?: string | undefined;
  onDismiss: () => void;
  LinkComponent?: React.ElementType | undefined;
}

/**
 * Said once, when a repository's reader has not yet seen it.
 *
 * Restarting savings history is a methodology change, not silent data loss,
 * so it is disclosed rather than absorbed. Three things the copy has to do:
 * name what restarted, say plainly that nothing else was deleted, and avoid
 * calling itself a cost reset -- no money moved and no spend record changed.
 *
 * The host decides whether this renders and remembers the dismissal. This
 * component only knows how to say it.
 */
export function SavingsResetNotice({
  methodologyHref,
  onDismiss,
  LinkComponent,
}: SavingsResetNoticeProps) {
  const A = LinkComponent ?? "a";
  return (
    <ReleaseNotice
      onDismiss={onDismiss}
      dismissLabel="Dismiss the savings accounting notice"
      title="Savings accounting has been upgraded."
      {...(methodologyHref
        ? {
            action: (
              <A
                href={methodologyHref}
                className="text-[var(--color-accent-primary)] hover:underline"
              >
                See what changed
              </A>
            ),
          }
        : {})}
    >
      Savings are now recorded one event per interaction, with the evidence behind each
      figure kept alongside it. Estimates produced by the earlier method could not be
      converted, so they were restarted rather than carried over. Your omission history and
      everything else in this repository were not deleted.
    </ReleaseNotice>
  );
}
