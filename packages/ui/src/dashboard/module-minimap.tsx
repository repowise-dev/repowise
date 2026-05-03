"use client";

import { useRef, useEffect, useState, useCallback } from "react";
import * as d3Force from "d3-force";
import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "../ui/tooltip";
import { Network } from "lucide-react";
import type { ModuleNode, ModuleEdge } from "@repowise-dev/types/graph";

interface ModuleMinimapProps {
  nodes: ModuleNode[];
  edges: ModuleEdge[];
  repoId: string;
}

interface SimNode extends d3Force.SimulationNodeDatum {
  id: string;
  fileCount: number;
  symbolCount: number;
  docCoverage: number;
  label: string;
}

interface SimLink {
  source: string;
  target: string;
  edgeCount: number;
}

export function ModuleMinimap({ nodes, edges, repoId }: ModuleMinimapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [simNodes, setSimNodes] = useState<SimNode[]>([]);
  const [simLinks, setSimLinks] = useState<Array<{ source: SimNode; target: SimNode; edgeCount: number }>>([]);
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });
  const [hoveredNode, setHoveredNode] = useState<string | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver((es) => {
      const first = es[0];
      if (!first) return;
      const { width, height } = first.contentRect;
      setDimensions({ width, height: Math.max(height, 200) });
    });
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (nodes.length === 0 || dimensions.width === 0) return;

    const sNodes: SimNode[] = nodes.map((n) => ({
      id: n.module_id,
      fileCount: n.file_count,
      symbolCount: n.symbol_count,
      docCoverage: n.doc_coverage_pct,
      label: n.module_id.split("/").pop() ?? n.module_id,
    }));

    const nodeIds = new Set(sNodes.map((n) => n.id));
    const sLinks: SimLink[] = edges
      .filter((e) => nodeIds.has(e.source) && nodeIds.has(e.target))
      .map((e) => ({ source: e.source, target: e.target, edgeCount: e.edge_count }));

    const sim = d3Force
      .forceSimulation(sNodes)
      .force(
        "link",
        d3Force.forceLink<SimNode, SimLink>(sLinks as never[]).id((d: SimNode) => d.id).distance(60),
      )
      .force("charge", d3Force.forceManyBody().strength(-120))
      .force("center", d3Force.forceCenter(dimensions.width / 2, dimensions.height / 2))
      .force("collision", d3Force.forceCollide().radius(20))
      .alpha(0.8)
      .alphaDecay(0.05);

    const resolveLink = (l: SimLink) => {
      const source = sNodes.find(
        (n) => n.id === (typeof l.source === "string" ? l.source : (l.source as SimNode).id),
      );
      const target = sNodes.find(
        (n) => n.id === (typeof l.target === "string" ? l.target : (l.target as SimNode).id),
      );
      if (!source || !target) return null;
      return { source, target, edgeCount: l.edgeCount };
    };

    sim.on("end", () => {
      setSimNodes([...sNodes]);
      setSimLinks(sLinks.map(resolveLink).filter((x): x is { source: SimNode; target: SimNode; edgeCount: number } => x !== null));
    });

    sim.tick(150);
    sim.stop();
    setSimNodes([...sNodes]);
    setSimLinks(sLinks.map(resolveLink).filter((x): x is { source: SimNode; target: SimNode; edgeCount: number } => x !== null));

    return () => { sim.stop(); };
  }, [nodes, edges, dimensions]);

  const getNodeRadius = useCallback(
    (n: SimNode) => Math.max(4, Math.min(16, Math.sqrt(n.fileCount) * 3)),
    [],
  );

  if (nodes.length === 0) return null;

  const { width, height } = dimensions;

  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center justify-between">
          <span className="flex items-center gap-2">
            <Network className="h-4 w-4 text-[var(--color-text-secondary)]" />
            Architecture
          </span>
          <a
            href={`/repos/${repoId}/graph`}
            className="text-[10px] text-[var(--color-accent-primary)] hover:underline font-normal"
          >
            Full graph
          </a>
        </CardTitle>
      </CardHeader>
      <CardContent className="pt-0">
        <div ref={containerRef} className="h-[220px] relative">
          {width > 0 && simNodes.length > 0 && (
            <TooltipProvider delayDuration={100}>
              <svg width={width} height={height}>
                {simLinks.map((link, i) => (
                  <line
                    key={i}
                    x1={link.source.x ?? 0}
                    y1={link.source.y ?? 0}
                    x2={link.target.x ?? 0}
                    y2={link.target.y ?? 0}
                    stroke="var(--color-border-default)"
                    strokeWidth={Math.min(link.edgeCount * 0.5, 3)}
                    opacity={
                      hoveredNode
                        ? hoveredNode === link.source.id || hoveredNode === link.target.id
                          ? 0.8
                          : 0.1
                        : 0.4
                    }
                    className="transition-opacity"
                  />
                ))}
                {simNodes.map((node) => {
                  const r = getNodeRadius(node);
                  const isHovered = hoveredNode === node.id;
                  const docColor =
                    node.docCoverage >= 70
                      ? "var(--color-node-documented)"
                      : node.docCoverage >= 30
                        ? "var(--color-warning)"
                        : "var(--color-node-undocumented)";
                  return (
                    <Tooltip key={node.id}>
                      <TooltipTrigger asChild>
                        <circle
                          cx={node.x ?? 0}
                          cy={node.y ?? 0}
                          r={isHovered ? r + 2 : r}
                          fill={docColor}
                          opacity={hoveredNode && !isHovered ? 0.3 : 0.8}
                          className="transition-all cursor-pointer"
                          onMouseEnter={() => setHoveredNode(node.id)}
                          onMouseLeave={() => setHoveredNode(null)}
                        />
                      </TooltipTrigger>
                      <TooltipContent side="top" className="text-xs">
                        <p className="font-medium font-mono">{node.id}</p>
                        <p className="text-[var(--color-text-tertiary)]">
                          {node.fileCount} files Â· {node.symbolCount} symbols Â· {Math.round(node.docCoverage)}% docs
                        </p>
                      </TooltipContent>
                    </Tooltip>
                  );
                })}
              </svg>
            </TooltipProvider>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
