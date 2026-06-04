"use client";

import { useState, useMemo, useCallback } from "react";
import { Folder, FolderOpen, FileText, ChevronRight, ChevronDown } from "lucide-react";
import { useArchitectureStore } from "../store/use-architecture-store";

interface TreeNode {
  name: string;
  fullPath: string;
  children: Map<string, TreeNode>;
  nodeId: string | null;
  language: string | null;
}

function buildTree(
  nodes: { id: string; file_path: string | null; language: string | null }[],
): TreeNode {
  const root: TreeNode = { name: "", fullPath: "", children: new Map(), nodeId: null, language: null };

  for (const node of nodes) {
    if (!node.file_path) continue;
    const segments = node.file_path.split("/");
    let current = root;
    for (let i = 0; i < segments.length; i++) {
      const segment = segments[i]!;
      const pathSoFar = segments.slice(0, i + 1).join("/");
      let child = current.children.get(segment);
      if (!child) {
        child = { name: segment, fullPath: pathSoFar, children: new Map(), nodeId: null, language: null };
        current.children.set(segment, child);
      }
      if (i === segments.length - 1) {
        child.nodeId = node.id;
        child.language = node.language;
      }
      current = child;
    }
  }

  return root;
}

export function FileExplorer() {
  const view = useArchitectureStore((s) => s.view);
  const selectedNodeId = useArchitectureStore((s) => s.selectedNodeId);
  const selectNode = useArchitectureStore((s) => s.selectNode);

  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const tree = useMemo(() => {
    if (!view) return null;
    const fileNodes = view.nodes.filter((n) => n.node_type === "file");
    return buildTree(fileNodes);
  }, [view]);

  const toggleDir = useCallback((path: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }, []);

  const handleFileClick = useCallback(
    (nodeId: string) => {
      selectNode(nodeId);
    },
    [selectNode],
  );

  if (!tree) return null;

  const sortedChildren = getSortedChildren(tree);

  return (
    <div style={{ padding: "6px 0" }}>
      {sortedChildren.map((child) => (
        <TreeNodeRow
          key={child.fullPath}
          node={child}
          depth={0}
          expanded={expanded}
          selectedNodeId={selectedNodeId}
          onToggle={toggleDir}
          onFileClick={handleFileClick}
        />
      ))}
      {sortedChildren.length === 0 && (
        <div style={{ padding: "12px", fontSize: 11, opacity: 0.5, textAlign: "center" }}>
          No file nodes available
        </div>
      )}
    </div>
  );
}

function TreeNodeRow({
  node,
  depth,
  expanded,
  selectedNodeId,
  onToggle,
  onFileClick,
}: {
  node: TreeNode;
  depth: number;
  expanded: Set<string>;
  selectedNodeId: string | null;
  onToggle: (path: string) => void;
  onFileClick: (nodeId: string) => void;
}) {
  const isDir = node.children.size > 0;
  const isExpanded = expanded.has(node.fullPath);
  const isSelected = node.nodeId !== null && node.nodeId === selectedNodeId;

  if (isDir) {
    const children = getSortedChildren(node);
    return (
      <>
        <button
          type="button"
          aria-label={`${isExpanded ? "Collapse" : "Expand"} ${node.name}`}
          aria-expanded={isExpanded}
          onClick={() => onToggle(node.fullPath)}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 4,
            width: "100%",
            padding: "3px 8px",
            paddingLeft: 8 + depth * 16,
            background: "none",
            border: "none",
            cursor: "pointer",
            color: "var(--color-text-primary)",
            fontSize: 11,
            textAlign: "left",
          }}
          onMouseEnter={(e) => { (e.currentTarget.style.background = "var(--color-bg-wash)"); }}
          onMouseLeave={(e) => { (e.currentTarget.style.background = "none"); }}
        >
          {isExpanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          {isExpanded ? <FolderOpen size={13} style={{ opacity: 0.7 }} /> : <Folder size={13} style={{ opacity: 0.7 }} />}
          <span>{node.name}</span>
        </button>
        {isExpanded &&
          children.map((child) => (
            <TreeNodeRow
              key={child.fullPath}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              selectedNodeId={selectedNodeId}
              onToggle={onToggle}
              onFileClick={onFileClick}
            />
          ))}
      </>
    );
  }

  return (
    <button
      type="button"
      aria-label={`Select ${node.name}`}
      onClick={() => node.nodeId && onFileClick(node.nodeId)}
      style={{
        display: "flex",
        alignItems: "center",
        gap: 4,
        width: "100%",
        padding: "3px 8px",
        paddingLeft: 8 + depth * 16 + 16,
        background: isSelected ? "var(--color-accent-muted, rgba(245,149,32,0.2))" : "none",
        border: "none",
        cursor: "pointer",
        color: isSelected
          ? "var(--color-accent-primary)"
          : "var(--color-text-primary)",
        fontSize: 11,
        textAlign: "left",
      }}
      onMouseEnter={(e) => {
        if (!isSelected) e.currentTarget.style.background = "var(--color-bg-wash)";
      }}
      onMouseLeave={(e) => {
        if (!isSelected) e.currentTarget.style.background = "none";
      }}
    >
      <FileText size={13} style={{ opacity: 0.7 }} />
      <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{node.name}</span>
      {node.language && (
        <span style={{ fontSize: 9, opacity: 0.5, flexShrink: 0 }}>{node.language}</span>
      )}
    </button>
  );
}

function getSortedChildren(node: TreeNode): TreeNode[] {
  const children = [...node.children.values()];
  children.sort((a, b) => {
    const aIsDir = a.children.size > 0;
    const bIsDir = b.children.size > 0;
    if (aIsDir !== bIsDir) return aIsDir ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
  return children;
}
