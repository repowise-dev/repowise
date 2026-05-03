"use client";

import { useRef, useEffect, useMemo } from "react";
import * as d3 from "d3-force";
import type { Hotspot } from "@repowise-dev/types/git";

interface ContributorNetworkProps {
  hotspots: Hotspot[];
}

interface ContributorNode extends d3.SimulationNodeDatum {
  id: string;
  fileCount: number;
  radius: number;
}

interface ContributorLink extends d3.SimulationLinkDatum<ContributorNode> {
  sharedFiles: number;
}

const PALETTE = [
  "#6366f1", "#ec4899", "#10b981", "#f59e0b", "#3b82f6", "#a855f7",
  "#14b8a6", "#f97316", "#84cc16", "#06b6d4", "#e11d48", "#8b5cf6",
];
const PALETTE_FALLBACK = "#6366f1";

export function ContributorNetwork({ hotspots }: ContributorNetworkProps) {
  const svgRef = useRef<SVGSVGElement>(null);

  const { nodes, links } = useMemo(() => {
    const ownerFiles = new Map<string, Set<string>>();
    for (const h of hotspots) {
      const owner = h.primary_owner;
      if (!owner) continue;
      if (!ownerFiles.has(owner)) ownerFiles.set(owner, new Set());
      ownerFiles.get(owner)!.add(h.file_path);
    }

    const nodeArr: ContributorNode[] = Array.from(ownerFiles.entries())
      .map(([id, files]) => ({
        id,
        fileCount: files.size,
        radius: Math.max(6, Math.min(24, Math.sqrt(files.size) * 4)),
      }))
      .sort((a, b) => b.fileCount - a.fileCount)
      .slice(0, 20);

    const nodeIds = new Set(nodeArr.map((n) => n.id));

    const linkArr: ContributorLink[] = [];
    const nodeList = Array.from(nodeIds);
    for (let i = 0; i < nodeList.length; i++) {
      for (let j = i + 1; j < nodeList.length; j++) {
        const idA = nodeList[i];
        const idB = nodeList[j];
        if (!idA || !idB) continue;
        const a = ownerFiles.get(idA);
        const b = ownerFiles.get(idB);
        if (!a || !b) continue;
        let shared = 0;
        for (const f of a) if (b.has(f)) shared++;
        if (shared > 0) {
          linkArr.push({ source: idA, target: idB, sharedFiles: shared });
        }
      }
    }

    return { nodes: nodeArr, links: linkArr };
  }, [hotspots]);

  useEffect(() => {
    const svg = svgRef.current;
    if (!svg || nodes.length === 0) return;

    const width = svg.clientWidth || 400;
    const height = svg.clientHeight || 300;

    while (svg.firstChild) svg.removeChild(svg.firstChild);

    const ns = "http://www.w3.org/2000/svg";

    const g = document.createElementNS(ns, "g");
    svg.appendChild(g);

    for (const n of nodes) {
      n.x = width / 2 + (Math.random() - 0.5) * 100;
      n.y = height / 2 + (Math.random() - 0.5) * 100;
    }

    const simulation = d3
      .forceSimulation(nodes)
      .force("link", d3.forceLink<ContributorNode, ContributorLink>(links).id((d) => d.id).distance(80).strength(0.3))
      .force("charge", d3.forceManyBody().strength(-120))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collide", d3.forceCollide<ContributorNode>().radius((d) => d.radius + 4));

    const linkElements: SVGLineElement[] = links.map((l) => {
      const line = document.createElementNS(ns, "line");
      line.setAttribute("stroke", "rgba(255,255,255,0.1)");
      line.setAttribute("stroke-width", String(Math.max(1, Math.min(4, l.sharedFiles))));
      g.appendChild(line);
      return line;
    });

    const nodeGroups: SVGGElement[] = nodes.map((n, i) => {
      const group = document.createElementNS(ns, "g");
      group.style.cursor = "pointer";

      const fill = PALETTE[i % PALETTE.length] ?? PALETTE_FALLBACK;
      const circle = document.createElementNS(ns, "circle");
      circle.setAttribute("r", String(n.radius));
      circle.setAttribute("fill", fill);
      circle.setAttribute("fill-opacity", "0.8");
      circle.setAttribute("stroke", fill);
      circle.setAttribute("stroke-opacity", "0.3");
      circle.setAttribute("stroke-width", "3");
      group.appendChild(circle);

      const text = document.createElementNS(ns, "text");
      text.setAttribute("text-anchor", "middle");
      text.setAttribute("dy", String(n.radius + 12));
      text.setAttribute("fill", "var(--color-text-secondary)");
      text.setAttribute("font-size", "9");
      text.textContent = n.id.length > 15 ? n.id.slice(0, 15) + "â€¦" : n.id;
      group.appendChild(text);

      const title = document.createElementNS(ns, "title");
      title.textContent = `${n.id}\n${n.fileCount} files owned`;
      group.appendChild(title);

      g.appendChild(group);
      return group;
    });

    simulation.on("tick", () => {
      linkElements.forEach((line, i) => {
        const l = links[i];
        if (!l) return;
        const s = l.source as ContributorNode;
        const t = l.target as ContributorNode;
        line.setAttribute("x1", String(s.x ?? 0));
        line.setAttribute("y1", String(s.y ?? 0));
        line.setAttribute("x2", String(t.x ?? 0));
        line.setAttribute("y2", String(t.y ?? 0));
      });

      nodeGroups.forEach((group, i) => {
        const n = nodes[i];
        if (!n) return;
        group.setAttribute("transform", `translate(${n.x ?? 0}, ${n.y ?? 0})`);
      });
    });

    return () => {
      simulation.stop();
    };
  }, [nodes, links]);

  if (nodes.length === 0) return null;

  return (
    <svg
      ref={svgRef}
      className="w-full h-64 md:h-80"
      style={{ background: "transparent" }}
    />
  );
}
