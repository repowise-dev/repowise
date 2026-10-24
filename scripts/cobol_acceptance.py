"""COBOL acceptance metrics: ground truth from source text vs a built index.

Usage::

    python scripts/cobol_acceptance.py <repo> [--json out.json]

``<repo>`` must hold ``.repowise/wiki.db`` from ``repowise init --no-prose``.
Truth is read with regexes that know nothing about the parser, so the two are
independent. Built for AWS CardDemo (``59cc6c2``); every count is generic.
"""

from __future__ import annotations

import argparse
import collections
import itertools
import json
import os
import re
import sqlite3
import sys

_PROGRAM_EXTS = {".cbl", ".cob", ".cobol"}
# A ``.dcl`` is taken as a COBOL DCLGEN member, which every CardDemo one is.
_COPYBOOK_EXTS = {".cpy", ".dcl"}
_JCL_EXTS = {".jcl", ".prc", ".proc"}
_BMS_EXTS = {".bms"}
_CSD_EXTS = {".csd"}
_CSD_COMMAND = re.compile(r"^\s*(?:DEFINE|ADD|ALTER|APPEND|COPY|DELETE|LIST|REMOVE)\s", re.I | re.M)
_NOT_A_PARAGRAPH = {"UNTIL", "VARYING", "WITH", "TEST", "FOREVER", "THRU", "THROUGH"}
_STRUCTURAL = ("calls", "imports", "framework")
_NOT_PROCEDURE = (
    r"^\s*(?:(?:IDENTIFICATION|ID|ENVIRONMENT|DATA)\s+DIVISION|PROGRAM-ID"
    r"|(?:FILE|WORKING-STORAGE|LOCAL-STORAGE|LINKAGE)\s+SECTION)\s*\."
)


def _walk(root: str) -> dict[str, dict[str, list[str]]]:
    """``{kind: {STEM: [relpath, ...]}}`` for programs, copybooks, JCL and BMS."""
    found: dict[str, dict[str, list[str]]] = {
        k: collections.defaultdict(list) for k in ("program", "copybook", "jcl", "bms", "csd")
    }
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", ".repowise")]
        for name in filenames:
            stem, ext = os.path.splitext(name)
            ext = ext.lower()
            rel = os.path.relpath(os.path.join(dirpath, name), root).replace("\\", "/")
            kind = (
                "program"
                if ext in _PROGRAM_EXTS
                else "copybook"
                if ext in _COPYBOOK_EXTS
                else "jcl"
                if ext in _JCL_EXTS
                else "bms"
                if ext in _BMS_EXTS
                else "csd"
                if ext in _CSD_EXTS
                else None
            )
            if kind:
                found[kind][stem.upper()].append(rel)
    return found


def _code_lines(path: str) -> list[tuple[int, str, str]]:
    """``(line, area_a_char, text)`` for fixed-format code lines (cols 8-72)."""
    out = []
    with open(path, encoding="latin-1") as fh:
        for number, raw in enumerate(fh, 1):
            raw = raw.rstrip("\r\n")
            if len(raw) > 6 and raw[6] in "*/":
                continue
            out.append((number, raw[7:8], raw[7:72]))
    return out


def truth(root: str) -> dict:
    files = _walk(root)
    progs, cpys, jcls, bms = (files[k] for k in ("program", "copybook", "jcl", "bms"))
    transactions = _csd_truth(root, files, progs, pairs := collections.defaultdict(set))
    paragraphs: set[tuple[str, str]] = set()
    performs: set[tuple[str, str]] = set()
    # A copybook whose code is the division header (a CICS ``COPY CENTRY.``) opens it too.
    headers = {
        stem
        for stem, rels in cpys.items()
        for rel in rels
        if re.match(
            r"\s*PROCEDURE\s+DIVISION\b",
            "\n".join(t for _, _, t in _code_lines(os.path.join(root, rel))),
            re.I,
        )
    }

    for rels in progs.values():
        for rel in rels:
            lines = _code_lines(os.path.join(root, rel))
            text = "\n".join(t for _, _, t in lines)
            own: set[str] = set()
            in_proc = False
            for _, area_a, body in lines:
                copied = re.match(r"\s*COPY\s+'?([A-Z0-9#@$-]+)", body, re.I)
                if re.search(r"PROCEDURE\s+DIVISION", body, re.I) or (
                    copied and copied.group(1).upper() in headers
                ):
                    in_proc = True
                    continue
                # A nested program's header and data division close the procedure.
                if re.search(_NOT_PROCEDURE, body, re.I):
                    in_proc = False
                if not in_proc:
                    continue
                m = re.match(r"([A-Z0-9][A-Z0-9-]*)(\s+SECTION)?\s*\.\s*$", body.rstrip(), re.I)
                if m and area_a != " " and m.group(1).upper() != "DECLARATIVES":
                    own.add(m.group(1).upper())
            paragraphs |= {(rel, p) for p in own}
            for m in re.finditer(r"\bPERFORM\s+([A-Z0-9][A-Z0-9-]*)", text, re.I):
                target = m.group(1).upper()
                if target in own and target not in _NOT_A_PARAGRAPH:
                    performs.add((rel, target))
            for m in re.finditer(r"^\s*COPY\s+'?([A-Z0-9#@$-]+)'?", text, re.I | re.M):
                for cpy in cpys.get(m.group(1).upper(), []):
                    pairs["copy"].add((rel, cpy))
                    pairs["copied"].add((rel, cpy))
            for m in re.finditer(r"EXEC\s+SQL\s+INCLUDE\s+([A-Z0-9#@$-]+)", text, re.I):
                for cpy in cpys.get(m.group(1).upper(), []):
                    pairs["include"].add((rel, cpy))
                    pairs["copied"].add((rel, cpy))
            for m in re.finditer(r"\bCALL\s+'([A-Z0-9#@$-]+)'", text, re.I):
                for dst in progs.get(m.group(1).upper(), []):
                    pairs["call"].add((rel, dst))
            literals = _literal_assignments(text)
            for m in re.finditer(r"EXEC\s+CICS\s+(?:LINK|XCTL)\b(.*?)END-EXEC", text, re.I | re.S):
                pm = re.search(r"PROGRAM\s*\(\s*('?)([A-Z0-9#@$-]+)'?\s*\)", m.group(1), re.I)
                if not pm:
                    continue
                name = pm.group(2).upper()
                targets = {name} if pm.group(1) else literals.get(name, set())
                for target in targets:
                    for dst in progs.get(target, []):
                        if dst != rel:
                            pairs["cics"].add((rel, dst))
            for m in re.finditer(
                r"EXEC\s+CICS\s+(?:RETURN|START)\b(.*?)END-EXEC", text, re.I | re.S
            ):
                tm = re.search(r"TRANSID\s*\(\s*('?)([A-Z0-9#@$-]+)'?\s*\)", m.group(1), re.I)
                if not tm:
                    continue
                name = tm.group(2).upper()
                for tran in {name} if tm.group(1) else literals.get(name, set()):
                    runs = transactions.get(tran, set())
                    for dst in progs.get(next(iter(runs)), []) if len(runs) == 1 else []:
                        if dst != rel:
                            pairs["transid"].add((rel, dst))
            for m in re.finditer(
                r"EXEC\s+CICS\s+(?:SEND|RECEIVE)\s+MAP\s*\(.*?END-EXEC", text, re.I | re.S
            ):
                ms = re.search(r"MAPSET\s*\(\s*'?([A-Z0-9#@$-]+)'?\s*\)", m.group(0), re.I)
                for dst in bms.get(ms.group(1).upper() if ms else "", []):
                    pairs["bms"].add((rel, dst))

    for rels in jcls.values():
        for rel in rels:
            with open(os.path.join(root, rel), encoding="latin-1") as fh:
                for raw in fh:
                    if raw.startswith("//*"):
                        continue
                    m = re.search(r"\bEXEC\s+PGM=([A-Z0-9#@$]+)", raw, re.I)
                    if m:
                        for dst in progs.get(m.group(1).upper(), []):
                            pairs["jcl"].add((rel, dst))

    return {
        "files": {k: sorted(r for v in d.values() for r in v) for k, d in files.items()},
        "paragraphs": paragraphs,
        "performs": performs,
        "pairs": pairs,
    }


def _csd_truth(root: str, files: dict, progs: dict, pairs: dict) -> dict[str, set[str]]:
    """CSD pairs (definer, program) into *pairs*; returns ``{TRANSACTION: {PROGRAM, ...}}``.

    Definitions are read from ``.csd`` files and from JCL in-stream data
    (``DD *`` up to ``/*`` or the next ``//`` statement).
    """
    transactions: dict[str, set[str]] = collections.defaultdict(set)
    for kind in ("csd", "jcl"):
        for rel in (r for rels in files[kind].values() for r in rels):
            with open(os.path.join(root, rel), encoding="latin-1") as fh:
                raw = fh.read().splitlines()
            if kind == "jcl":
                kept, inside = [], False
                for line in raw:
                    if line.startswith("/"):
                        inside = bool(re.search(r"\bDD\s+(\*|DATA)", line, re.I))
                    elif inside:
                        kept.append(line)
                raw = kept
            text = "\n".join(line for line in raw if not line.startswith("*"))
            starts = [m.start() for m in _CSD_COMMAND.finditer(text)] + [len(text)]
            for a, b in itertools.pairwise(starts):
                chunk = text[a:b]
                d = re.match(r"\s*DEFINE\s+(PROGRAM|TRANSACTION)\s*\(\s*(\S+?)\s*\)", chunk, re.I)
                if not d:
                    continue
                name = d.group(2).upper()
                if d.group(1).upper() == "TRANSACTION":
                    pm = re.search(r"\sPROGRAM\s*\(\s*(\S+?)\s*\)", chunk[d.end() :], re.I)
                    if not pm:
                        continue
                    name = pm.group(1).upper()
                    transactions[d.group(2).upper()].add(name)
                for dst in progs.get(name, []):
                    pairs["csd"].add((rel, dst))
    return transactions


def _literal_assignments(text: str) -> dict[str, set[str]]:
    """``{VAR: {'LIT', ...}}`` from ``VAR ... VALUE 'LIT'``, ``MOVE 'LIT' TO VAR``, and
    ``MOVE CONST TO VAR`` where ``CONST`` has a VALUE literal."""
    values: dict[str, set[str]] = collections.defaultdict(set)
    for m in re.finditer(
        r"^\s*\d\d\s+([A-Z0-9-]+)\s+PIC[^.]*?VALUE\s+'([A-Z0-9#@$-]+)'", text, re.I | re.M
    ):
        values[m.group(1).upper()].add(m.group(2).upper())
    out = collections.defaultdict(set, {k: set(v) for k, v in values.items()})
    for m in re.finditer(r"\bMOVE\s+('?)([A-Z0-9#@$-]+)'?\s+TO\s+([A-Z0-9-]+)", text, re.I):
        source, target = m.group(2).upper(), m.group(3).upper()
        out[target] |= {source} if m.group(1) else values.get(source, set())
    return out


def index_view(db: str) -> dict:
    con = sqlite3.connect(db)
    nodes = {
        r[0]: r
        for r in con.execute(
            "select node_id, node_type, language, file_path, name, kind from graph_nodes"
        )
    }

    def file_of(node_id: str) -> str:
        row = nodes.get(node_id)
        if row is None:
            return node_id
        return row[0] if row[1] == "file" else (row[3] or node_id)

    edges = con.execute(
        "select source_node_id, target_node_id, edge_type from graph_edges"
    ).fetchall()
    # The per-file ``__module__`` node is graph plumbing, not a symbol ``wiki_symbols`` holds.
    symbols = [
        r for r in nodes.values() if r[1] == "symbol" and r[2] == "cobol" and r[4] != "__module__"
    ]
    procs = {(r[3], r[4].upper()) for r in symbols if r[5] == "function"}
    same_file_calls: set[tuple[str, str]] = set()
    cross_perform: list[tuple[str, str]] = []
    file_pairs: dict[str, set[tuple[str, str]]] = collections.defaultdict(set)
    for src, dst, etype in edges:
        fs, ft = file_of(src), file_of(dst)
        if etype == "calls":
            target = nodes.get(dst)
            if fs == ft and target is not None:
                same_file_calls.add((fs, (target[4] or "").upper()))
            elif target is not None and target[5] == "function" and target[2] == "cobol":
                cross_perform.append((fs, ft))
        if etype in _STRUCTURAL and fs != ft and dst in nodes:
            file_pairs[etype].add((fs, ft))
    return {
        "nodes": nodes,
        "cobol_symbols": len(symbols),
        "procs": procs,
        "same_file_calls": same_file_calls,
        "cross_perform": cross_perform,
        "file_pairs": file_pairs,
    }


def metrics(root: str, db: str) -> dict:
    t, ix = truth(root), index_view(db)
    any_pairs = set().union(*ix["file_pairs"].values()) if ix["file_pairs"] else set()
    imports = ix["file_pairs"].get("imports", set())

    def ratio(found: int, total: int) -> str:
        return f"{found}/{total} ({found / total:.0%})" if total else "0/0"

    per_prog = collections.defaultdict(set)
    for rel, name in t["paragraphs"]:
        per_prog[rel].add(name)
    whole = sum(
        1
        for rel in t["files"]["program"]
        if all((rel, n) in ix["procs"] for n in per_prog.get(rel, ()))
    )
    cobol_files = t["files"]["program"] + t["files"]["copybook"]
    touched = {f for pair in any_pairs for f in pair}
    pair_hits = {
        kind: ratio(
            len(t["pairs"][kind] & (imports if kind in ("copy", "include") else any_pairs)),
            len(t["pairs"][kind]),
        )
        for kind in ("copy", "include", "call", "cics", "jcl", "bms", "csd", "transid")
    }
    return {
        "cobol_symbols": ix["cobol_symbols"],
        "programs_whole": ratio(whole, len(t["files"]["program"])),
        "paragraph_recall": ratio(len(t["paragraphs"] & ix["procs"]), len(t["paragraphs"])),
        "perform_recall": ratio(len(t["performs"] & ix["same_file_calls"]), len(t["performs"])),
        **{f"{k}_pairs": v for k, v in pair_hits.items()},
        # A PERFORM into a copybook the program copies is right; any other is not.
        "cross_program_perform_edges": sum(
            1 for pair in ix["cross_perform"] if pair not in t["pairs"]["copied"]
        ),
        "cobol_files_isolated": ratio(
            sum(1 for f in cobol_files if f not in touched), len(cobol_files)
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo")
    ap.add_argument("--json", help="write the metrics here as JSON")
    args = ap.parse_args()
    result = metrics(args.repo, os.path.join(args.repo, ".repowise", "wiki.db"))
    for key, value in result.items():
        print(f"{key:30s} {value}")
    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
