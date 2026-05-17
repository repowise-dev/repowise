"use client";

/**
 * C4 architecture diagrams — `/repos/[id]/c4`.
 *
 * Thin wrapper around `<C4Diagram>` from @repowise-dev/ui/c4. State (level +
 * active container) is mirrored into URL params via nuqs so refresh + share
 * preserve the view.
 */

import { use, useCallback } from "react";
import { useQueryState, parseAsInteger, parseAsString } from "nuqs";
import { C4Diagram, type C4Level } from "@repowise-dev/ui/c4";
import { useC4L1, useC4L2, useC4L3 } from "@/lib/hooks/use-c4";
import { useC4DocsPathSet } from "@/lib/hooks/use-c4-context";
import { useRepo } from "@/lib/hooks/use-repo";
import { getC4Mermaid } from "@/lib/api/c4";
import { C4DetailPanelHost } from "@/components/c4/c4-detail-panel-host";

function clampLevel(n: number | null): C4Level {
  return n === 1 ? 1 : n === 3 ? 3 : 2;
}

export default function C4Page({ params }: { params: Promise<{ id: string }> }) {
  const { id: repoId } = use(params);
  const { repo } = useRepo(repoId);

  const [levelParam, setLevelParam] = useQueryState(
    "level",
    parseAsInteger.withDefault(2),
  );
  const [containerParam, setContainerParam] = useQueryState(
    "container",
    parseAsString,
  );

  const level = clampLevel(levelParam);
  const activeContainerId = containerParam || null;

  const { view: l1View, error: l1Err, isLoading: l1Loading } = useC4L1(level === 1 ? repoId : null);
  const { view: l2View, error: l2Err, isLoading: l2Loading } = useC4L2(level >= 2 ? repoId : null);
  const { view: l3View, error: l3Err, isLoading: l3Loading } = useC4L3(
    level === 3 ? repoId : null,
    level === 3 ? activeContainerId : null,
  );

  const setLevel = useCallback(
    (next: C4Level) => {
      void setLevelParam(next);
      if (next !== 3) void setContainerParam(null);
    },
    [setContainerParam, setLevelParam],
  );

  const drillInto = useCallback(
    (containerId: string) => {
      void setLevelParam(3);
      void setContainerParam(containerId);
    },
    [setContainerParam, setLevelParam],
  );

  const drillOut = useCallback(() => {
    if (level === 3) {
      void setLevelParam(2);
      void setContainerParam(null);
    } else if (level === 2) {
      void setLevelParam(1);
    }
  }, [level, setContainerParam, setLevelParam]);

  const loading =
    level === 1 ? l1Loading : level === 2 ? l2Loading : l3Loading;
  const error =
    level === 1 ? l1Err : level === 2 ? l2Err : l3Err;

  const { pathSet: docsPathSet, pageIdByPath } = useC4DocsPathSet(repoId);

  const fetchMermaid = useCallback(
    () => getC4Mermaid(repoId, level, activeContainerId),
    [activeContainerId, level, repoId],
  );

  return (
    <div className="flex flex-col h-full">
      <div className="shrink-0 px-4 sm:px-6 py-3 border-b border-[var(--color-border-default)]">
        <h1 className="text-lg font-semibold text-[var(--color-text-primary)]">
          C4 Architecture
        </h1>
        <p className="text-xs text-[var(--color-text-secondary)] mt-0.5">
          System context, containers, and components — drill in to navigate.
        </p>
      </div>
      <div className="flex-1 min-h-0">
        <C4Diagram
          level={level}
          activeContainerId={activeContainerId}
          systemName={repo?.name ?? "System"}
          l1View={l1View}
          l2View={l2View}
          l3View={l3View}
          loading={loading}
          error={error}
          onLevelChange={setLevel}
          onDrillInto={drillInto}
          onDrillOut={drillOut}
          docsPathSet={docsPathSet}
          fetchMermaid={fetchMermaid}
          renderInspector={({ data, onClose, onDrillIn }) => (
            <C4DetailPanelHost
              repoId={repoId}
              data={data}
              pageIdByPath={pageIdByPath}
              onClose={onClose}
              onDrillIn={onDrillIn}
            />
          )}
        />
      </div>
    </div>
  );
}
