"use client";

/**
 * DSM grid — renders a `DsmMatrix` as a services × services table. Rows depend on
 * columns: a filled cell `[row][col]` means the row service depends on the column
 * service, tinted by the dominant edge kind. Rule violations are ringed red,
 * dependency-cycle cells amber. The diagonal is inert (a service vs itself).
 *
 * Pure presentation: the host builds the matrix with `buildDsm` and passes it in.
 * Designed to scale — the cell grid is plain CSS, no per-cell React Flow.
 */

import { useMemo, useState } from "react";
import type { DsmCell, DsmMatrix } from "@repowise-dev/types";
import { EmptyState } from "../../shared/empty-state";
import { edgeKindStyle } from "../system-map/edge-kinds";

export interface DsmMatrixViewProps {
  matrix: DsmMatrix;
  /** Optional click handler for a present cell (drill to the dependency). */
  onSelectCell?: (cell: DsmCell) => void;
}

const CELL = 30;
const HEADER = 150;

function cellBackground(cell: DsmCell, isDiagonal: boolean): string {
  if (isDiagonal) return "var(--color-bg-subtle)";
  if (!cell.present) return "transparent";
  if (cell.violation) return "color-mix(in srgb, var(--color-risk-high) 28%, transparent)";
  if (cell.cycle) return "color-mix(in srgb, var(--color-warning) 26%, transparent)";
  const color = edgeKindStyle(cell.kind ?? "http").color;
  return `color-mix(in srgb, ${color} 34%, transparent)`;
}

function cellRing(cell: DsmCell): string {
  if (cell.violation) return "inset 0 0 0 2px var(--color-risk-high)";
  if (cell.cycle) return "inset 0 0 0 2px var(--color-warning)";
  return "none";
}

export function DsmMatrixView({ matrix, onSelectCell }: DsmMatrixViewProps) {
  const [hover, setHover] = useState<{ row: number; col: number } | null>(null);

  const counts = useMemo(() => {
    let present = 0;
    let violations = 0;
    let cycles = 0;
    for (const row of matrix.cells) {
      for (const cell of row) {
        if (cell.present) present += 1;
        if (cell.violation) violations += 1;
        if (cell.cycle) cycles += 1;
      }
    }
    return { present, violations, cycles };
  }, [matrix]);

  if (matrix.axis.length === 0) {
    return (
      <EmptyState
        title="No services to chart"
        description="The dependency-structure matrix appears once the workspace has indexed services with cross-repo relationships."
      />
    );
  }

  const n = matrix.axis.length;

  return (
    <div>
      <div
        style={{
          display: "flex",
          gap: 16,
          fontSize: 12,
          color: "var(--color-text-tertiary)",
          marginBottom: 10,
        }}
      >
        <span>
          <strong style={{ color: "var(--color-text-secondary)" }}>{n}</strong> services
        </span>
        <span>
          <strong style={{ color: "var(--color-text-secondary)" }}>{counts.present}</strong>{" "}
          dependencies
        </span>
        {counts.violations > 0 && (
          <span style={{ color: "var(--color-risk-high)" }}>{counts.violations} violation(s)</span>
        )}
        {counts.cycles > 0 && (
          <span style={{ color: "var(--color-warning)" }}>{counts.cycles} cycle cell(s)</span>
        )}
      </div>

      <div style={{ overflow: "auto", maxWidth: "100%" }}>
        <div
          role="grid"
          aria-label="Dependency-structure matrix"
          style={{
            display: "grid",
            gridTemplateColumns: `${HEADER}px repeat(${n}, ${CELL}px)`,
            width: "max-content",
            fontSize: 11,
          }}
        >
          {/* Top-left corner + column headers */}
          <div style={{ position: "sticky", left: 0, zIndex: 2, background: "var(--color-bg-canvas)" }} />
          {matrix.labels.map((label, j) => (
            <div
              key={`col-${matrix.axis[j]}`}
              title={matrix.axis[j]}
              style={{
                height: HEADER,
                display: "flex",
                alignItems: "flex-end",
                justifyContent: "center",
                color:
                  hover?.col === j
                    ? "var(--color-text-primary)"
                    : "var(--color-text-tertiary)",
                writingMode: "vertical-rl",
                transform: "rotate(180deg)",
                whiteSpace: "nowrap",
                overflow: "hidden",
                textOverflow: "ellipsis",
                paddingBottom: 4,
              }}
            >
              {label}
            </div>
          ))}

          {/* Rows */}
          {matrix.cells.map((row, i) => (
            <RowCells
              key={`row-${matrix.axis[i]}`}
              i={i}
              row={row}
              label={matrix.labels[i] ?? ""}
              axisId={matrix.axis[i] ?? ""}
              hover={hover}
              onHover={setHover}
              {...(onSelectCell ? { onSelectCell } : {})}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function RowCells({
  i,
  row,
  label,
  axisId,
  hover,
  onHover,
  onSelectCell,
}: {
  i: number;
  row: DsmCell[];
  label: string;
  axisId: string;
  hover: { row: number; col: number } | null;
  onHover: (h: { row: number; col: number } | null) => void;
  onSelectCell?: (cell: DsmCell) => void;
}) {
  return (
    <>
      <div
        title={axisId}
        style={{
          position: "sticky",
          left: 0,
          zIndex: 1,
          height: CELL,
          display: "flex",
          alignItems: "center",
          paddingRight: 8,
          justifyContent: "flex-end",
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
          background: "var(--color-bg-canvas)",
          color: hover?.row === i ? "var(--color-text-primary)" : "var(--color-text-secondary)",
        }}
      >
        {label}
      </div>
      {row.map((cell, j) => {
        const isDiagonal = i === j;
        const interactive = cell.present && !isDiagonal && Boolean(onSelectCell);
        const title = cell.present
          ? `${cell.from_id} → ${cell.to_id} (${cell.kind ?? ""})${
              cell.violation ? " · violation" : cell.cycle ? " · cycle" : ""
            }`
          : undefined;
        return (
          <div
            key={`cell-${i}-${j}`}
            role="gridcell"
            title={title}
            onMouseEnter={() => onHover({ row: i, col: j })}
            onMouseLeave={() => onHover(null)}
            onClick={interactive ? () => onSelectCell?.(cell) : undefined}
            style={{
              width: CELL,
              height: CELL,
              boxSizing: "border-box",
              border: "1px solid var(--color-border-subtle)",
              background: cellBackground(cell, isDiagonal),
              boxShadow: cellRing(cell),
              cursor: interactive ? "pointer" : "default",
              outline:
                hover && (hover.row === i || hover.col === j)
                  ? "1px solid var(--color-border-default)"
                  : "none",
            }}
          />
        );
      })}
    </>
  );
}
