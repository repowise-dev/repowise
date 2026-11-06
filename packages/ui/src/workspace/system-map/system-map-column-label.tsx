"use client";

/** A focus column's heading on the canvas: inert text, not a card. */

import { memo } from "react";
import type { NodeProps } from "@xyflow/react";
import type { ColumnLabelData } from "./focus-labels";
import { MICRO_LABEL } from "./system-map-filters";

function SystemMapColumnLabelInner({ data }: NodeProps) {
  const { label } = data as unknown as ColumnLabelData;
  return (
    <div role="heading" aria-level={3} className={`${MICRO_LABEL} pointer-events-none whitespace-nowrap`}>
      {label}
    </div>
  );
}

export const SystemMapColumnLabel = memo(SystemMapColumnLabelInner);
