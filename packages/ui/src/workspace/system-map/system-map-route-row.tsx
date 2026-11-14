"use client";

/**
 * One route between two services, as a rail row: the route in mono (linked
 * to the contract when the host routes there), the calling file and the
 * serving file, and the matching rule only when it needs checking or a
 * gateway made it. A package row names its manifest; a co-change row its
 * file pair. Presentation only.
 */

import type { ElementType } from "react";
import type { ServiceLinkEntry } from "@repowise-dev/types/workspace";
import { FilePath } from "../../shared/file-path";
import { linkStrategyCopy, strategyNeedsCheck } from "../link-provenance";
import { routeTitle } from "./routes-between";
import type { ContractRef } from "./system-map-drawer";
import { formatNumber } from "../../lib/format";

export interface RouteRowProps {
  row: ServiceLinkEntry;
  contractHref?: ((ref: ContractRef) => string) | undefined;
  LinkComponent?: ElementType | undefined;
}

const MONO_10 = "font-mono text-[10px] text-[var(--color-text-secondary)]";

function Title({ row, contractHref, LinkComponent }: RouteRowProps) {
  const title = routeTitle(row);
  const l = row.link;
  if (!l || !contractHref) return <span className="font-mono text-xs [overflow-wrap:anywhere]">{title}</span>;
  const A = LinkComponent ?? "a";
  return (
    <A
      href={contractHref({ contract_id: l.contract_id, repo: l.provider_repo, file_path: l.provider_file })}
      className="font-mono text-xs text-[var(--color-accent-primary)] hover:underline [overflow-wrap:anywhere]"
    >
      {title}
    </A>
  );
}

/** The matching rule, named only when the reader should check it or a gateway made the link. */
function StrategyNote({ strategy }: { strategy: string | null | undefined }) {
  if (!strategyNeedsCheck(strategy) && strategy !== "gateway_route") return null;
  const copy = linkStrategyCopy(strategy);
  return copy ? (
    <span className="text-[10px] text-[var(--color-text-secondary)]" title={copy.meaning}>
      {copy.label}
    </span>
  ) : null;
}

function Evidence({ row }: { row: ServiceLinkEntry }) {
  const l = row.link;
  if (l) {
    return (
      <>
        <span className={MONO_10}>
          called from <FilePath path={`${l.consumer_repo}/${l.consumer_file}`} mode="full" className="text-[10px]" />
        </span>
        <span className={MONO_10}>
          served by <FilePath path={`${l.provider_repo}/${l.provider_file}`} mode="full" className="text-[10px]" />
        </span>
        <StrategyNote strategy={l.strategy} />
      </>
    );
  }
  const d = row.package_dep;
  if (d) {
    return (
      <span className={MONO_10}>
        declared in <FilePath path={`${d.source_repo}/${d.source_manifest}`} mode="full" className="text-[10px]" />
      </span>
    );
  }
  const c = row.co_change;
  if (c) {
    return (
      <span className="text-[10px] tabular-nums text-[var(--color-text-secondary)]">
        changed together {formatNumber(c.frequency)} {c.frequency === 1 ? "time" : "times"}
      </span>
    );
  }
  return null;
}

export function RouteRow(props: RouteRowProps) {
  return (
    <li className="flex flex-col gap-0.5 py-2">
      <Title {...props} />
      <Evidence row={props.row} />
    </li>
  );
}
