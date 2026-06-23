"""(b) Prompt construction for domain synthesis.

Pure string builders - no LLM, no I/O. Two prompts:

* :func:`build_domain_naming_prompt` groups structural layer clusters into
  behavior-oriented capability domains. Membership is constrained to the layer
  ids actually supplied, so the model cannot invent a cluster.
* :func:`build_flow_prompt` extracts ordered flows + steps for one domain. Each
  step's implementing files are constrained to the domain's member file paths,
  so node-id resolution can reject any hallucinated membership downstream.
"""

from __future__ import annotations

from .context import FileContext, LayerCluster

DOMAIN_NAMING_SYSTEM = (
    "You are a software architecture analyst. You group low-level code clusters "
    "into a small set of behavior-oriented capability domains that describe what "
    "the system *does* (e.g. 'Indexing Pipeline', 'Code Health', 'Persistence'). "
    "Output valid JSON only. No preamble, no markdown fences."
)

FLOW_EXTRACTION_SYSTEM = (
    "You are a software architecture analyst. For one capability domain you "
    "describe the key end-to-end processes (flows) it performs, each as an "
    "ordered list of steps, mapping every step to the specific files that "
    "implement it. Output valid JSON only. No preamble, no markdown fences."
)


def build_domain_naming_prompt(clusters: list[LayerCluster]) -> str:
    lines = [
        "Group the code clusters below into 4-10 capability domains describing "
        "what the system does. Rules:",
        "- A domain's name is behavior-oriented (a capability), not a code layer "
        "name. Keep it 1-4 words.",
        "- member_layer_ids must each be one of the cluster ids listed below, "
        "copied verbatim. Do not invent ids.",
        "- Assign each cluster to at most one domain. It is fine to leave a "
        "purely incidental cluster (docs, tooling) out.",
        "- slug is a short lowercase-kebab identifier for the domain.",
        "",
        "Clusters:",
    ]
    for c in clusters:
        lines.append(f'\n--- cluster id "{c.layer_id}" ---')
        lines.append(f"Heuristic name: {c.name}")
        if c.description:
            lines.append(f"Description: {c.description}")
        lines.append(f"File count: {c.file_count}")
        if c.top_files:
            lines.append(f"Representative files: {', '.join(c.top_files)}")
    lines.append("")
    lines.append(
        'Respond with: {"domains": [{"slug": "...", "name": "...", '
        '"summary": "one sentence", "member_layer_ids": ["..."]}]}'
    )
    return "\n".join(lines)


def build_flow_prompt(
    domain_name: str,
    domain_summary: str,
    members: list[FileContext],
    internal_edges: list[tuple[str, str]],
) -> str:
    lines = [
        f'Describe the key processes (flows) of the "{domain_name}" domain.',
    ]
    if domain_summary:
        lines.append(f"Domain summary: {domain_summary}")
    lines += [
        "",
        "Rules:",
        "- Produce 1-4 flows. Each flow is a real end-to-end process in this "
        "domain (e.g. 'Index a repository from scratch').",
        "- Each flow has ordered steps starting at 1, contiguous, no gaps.",
        "- Every step's `implements` is a list of file paths chosen ONLY from "
        "the member files listed below, copied verbatim. Never invent a path.",
        "- A step maps to at least one member file. Keep summaries to one line.",
        "- slug is a short lowercase-kebab identifier for the flow.",
        "",
        "Member files (path -- summary):",
    ]
    for m in members:
        summary = m.summary.strip().replace("\n", " ")
        if len(summary) > 160:
            summary = summary[:157] + "..."
        lines.append(f"- {m.path}" + (f" -- {summary}" if summary else ""))
    if internal_edges:
        lines.append("")
        lines.append("Key internal dependencies (A is imported by B):")
        for src, tgt in internal_edges:
            lines.append(f"- {src} <- {tgt}")
    lines.append("")
    lines.append(
        'Respond with: {"flows": [{"slug": "...", "name": "...", '
        '"summary": "one sentence", "steps": [{"order": 1, "name": "...", '
        '"summary": "one line", "implements": ["path/to/file.py"]}]}]}'
    )
    return "\n".join(lines)
