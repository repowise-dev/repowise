"use client";

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import useSWR from "swr";
import { ContractDrawer, type ContractRef } from "@repowise-dev/ui/workspace/contract-drawer";
import { asContractSchema, type ContractEntry } from "@repowise-dev/ui/workspace/contract-facts";
import { fileEntityPath, symbolEntityPath } from "@repowise-dev/ui/shared/entity";
import { getWorkspaceContractDetail, getWorkspaceTestImpact } from "@/lib/api/workspace";
import { useWorkspaceBreakingChanges } from "@/lib/hooks/use-workspace";
import { contractDetailHref } from "./contract-href";

/** Open the contract drawer. A row can pass itself so the panel paints before the fetch. */
type OpenContract = (ref: ContractRef, row?: ContractEntry) => void;

const OpenContext = createContext<OpenContract>(() => {});

export function useOpenContract(): OpenContract {
  return useContext(OpenContext);
}

/** The deep-link params the drawer owns; stripped from the URL when it closes. */
const DEEP_LINK_PARAMS = ["contract", "repo", "file"] as const;

/**
 * The one contract drawer on the page, and the context every table uses to
 * open it.
 *
 * A provider around server-rendered sections rather than a client page: the
 * headings, prose and figures stay on the server, and only the rows that open
 * the drawer are client leaves. The detail and the test impact are fetched
 * when the drawer opens, one request each, keyed so reopening a contract costs
 * nothing.
 */
export function ContractDrawerProvider({
  children,
  repoIds,
  initial,
}: {
  children: ReactNode;
  /** Repo alias to indexed repo id. A never-indexed repo has no entry. */
  repoIds: Record<string, string>;
  /** `?contract=&repo=&file=` on load. */
  initial: ContractRef | null;
}) {
  const [selected, setSelected] = useState<{ ref: ContractRef; row?: ContractEntry } | null>(
    initial ? { ref: initial } : null,
  );
  const ref = selected?.ref ?? null;

  const detail = useSWR(
    ref ? ["workspace:contract", ref.repo, ref.file_path, ref.contract_id] : null,
    () =>
      getWorkspaceContractDetail({
        repo: ref!.repo,
        file: ref!.file_path,
        id: ref!.contract_id,
      }),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );
  const contract = detail.data?.contract ?? selected?.row ?? null;
  const isProvider = contract?.role === "provider";

  // Only a provider has consumers whose tests guard it, and the lookup opens
  // the consumer indexes, so a consumer never pays for it.
  const tests = useSWR(
    ref && isProvider ? ["workspace:test-impact", ref.repo, ref.file_path] : null,
    () => getWorkspaceTestImpact(ref!.repo, [ref!.file_path]),
    { revalidateOnFocus: false, shouldRetryOnError: false },
  );
  // Same key as the section above, so this costs no second request.
  const { data: breaking } = useWorkspaceBreakingChanges();

  const open = useCallback<OpenContract>((next, row) => {
    setSelected(row ? { ref: next, row } : { ref: next });
  }, []);

  // True until the drawer a deep link opened is closed. Only then are its
  // params stripped: `contract` + `repo` without `file` is a list filter, and
  // must survive closing a drawer opened from a row.
  const deepLinkOpen = useRef(initial !== null);

  const close = useCallback(() => {
    setSelected(null);
    if (!deepLinkOpen.current) return;
    deepLinkOpen.current = false;
    // So a reload does not reopen the drawer the reader just closed.
    const url = new URL(window.location.href);
    for (const p of DEEP_LINK_PARAMS) url.searchParams.delete(p);
    window.history.replaceState(null, "", `${url.pathname}${url.search}${url.hash}`);
  }, []);

  const codeLinks = useMemo(
    () => ({
      symbolHref: (repo: string, symbolId: string) => {
        const id = repoIds[repo];
        return id ? symbolEntityPath(`/repos/${id}`, symbolId) : null;
      },
      fileHref: (repo: string, file: string) => {
        const id = repoIds[repo];
        return id ? fileEntityPath(`/repos/${id}`, file) : null;
      },
    }),
    [repoIds],
  );

  const notFound =
    detail.error && (detail.error as { status?: number }).status === 404
      ? "This contract is not in the workspace any more. It may have been renamed or removed since the link was made."
      : detail.error
        ? "The contract could not be read. Try again, or open the full page."
        : null;

  return (
    <OpenContext.Provider value={open}>
      {children}
      <ContractDrawer
        open={ref !== null}
        onOpenChange={(next) => {
          if (!next) close();
        }}
        contract={contract}
        pendingId={ref?.contract_id}
        loading={detail.isLoading}
        error={notFound}
        links={detail.data?.links ?? []}
        unmatchedReason={detail.data?.unmatched_reason ?? null}
        schema={asContractSchema(detail.data?.contract_schema ?? null)}
        breakingChanges={
          ref
            ? (breaking?.changes ?? []).filter(
                (c) =>
                  c.contract_id === ref.contract_id &&
                  (!isProvider || (c.provider_repo === ref.repo && c.provider_file === ref.file_path)),
              )
            : []
        }
        testImpact={isProvider ? (tests.data ?? null) : null}
        testImpactLoading={isProvider && tests.isLoading}
        testImpactError={
          tests.error
            ? "Test impact could not be read. It is answered from the consumer repositories' own indexes, and that lookup failed."
            : null
        }
        codeLinks={codeLinks}
        fullPageHref={ref ? contractDetailHref(ref) : undefined}
        onSelectContract={(next) => open(next)}
      />
    </OpenContext.Provider>
  );
}
