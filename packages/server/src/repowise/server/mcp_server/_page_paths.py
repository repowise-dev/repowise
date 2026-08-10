"""Which page identifiers name a file a caller can actually open.

A page id is ``{page_type}:{target_path}``, and ``target_path`` means a
different thing for each type. For a file page it is the file. For a module
page it is a structural group key, which reads like a directory. For an SCC
page it is ``scc-<hash>``. For an onboarding page it is a slot name. For the
repository overview it is the repository's name.

Every one of those is a legitimate thing to *rank*, and none of them is a
legitimate thing to hand back in a field named ``path`` or ``file``. An agent
told to read ``onboarding/how_it_works`` or ``pkg/cmd/release`` gets an error,
and nothing in the response says the string was never a path. This module is
the one place that distinction is written down, so the tools cannot drift on
it.
"""

from __future__ import annotations

# Page types whose ``target_path`` is a real repository-relative file path.
#
# ``symbol_spotlight`` is included because its target_path is ``file.py::Sym``:
# a page id built from a file path, so the file is recoverable by splitting.
# Everything absent from this set is named by its members or by a curated id
# (module_page by a structural group key, scc_page by ``scc-<hash>``,
# layer_page by a layer name, repo_overview by the repo name,
# architecture_diagram and onboarding by a slot), and no amount of string
# manipulation turns those into something openable.
FILE_BACKED_PAGE_TYPES: frozenset[str] = frozenset(
    {
        "file_page",
        "symbol_spotlight",
        "api_contract",
        "infra_page",
    }
)


def file_path_of(page_type: str | None, target_path: str | None) -> str | None:
    """The file *page_type*/*target_path* names, or ``None`` if it names none.

    Splits a ``symbol_spotlight``'s ``file.py::Symbol`` back to ``file.py``.
    Returns ``None`` rather than the raw string for every page type that is not
    file-backed, so a caller cannot accidentally serve a page id as a path by
    forgetting to check the type.
    """
    if page_type not in FILE_BACKED_PAGE_TYPES:
        return None
    path = (target_path or "").split("::", 1)[0].strip()
    return path or None


def hit_file_path(hit: dict) -> str | None:
    """The file a single search hit resolves to, whatever kind of hit it is.

    Two hit shapes reach this. A page hit carries ``page_type`` and
    ``target_path`` and is resolved by :func:`file_path_of`, so a module page
    or an onboarding slot resolves to nothing. A symbol-index hit carries no
    page type at all and names its file directly in ``file``.

    The page-type branch is checked first and is authoritative: a page hit that
    resolves to no file must not fall through to a looser key and smuggle a
    page id out as a path.

    A hit with **no** page type is a different case from a hit with an
    unrecognised one, and they are deliberately not treated alike. An absent
    type means the hit was never hydrated against the Page table, and dropping
    it would silently lose a real file over a bookkeeping gap; so those fall
    back to whatever path they carry. An unrecognised type means the page was
    hydrated and classified as naming no file, which is an answer, not a gap.
    """
    if hit.get("page_type"):
        return file_path_of(hit.get("page_type"), hit.get("target_path"))
    raw = hit.get("file") or hit.get("target_path") or ""
    return raw.split("::", 1)[0].strip() or None


def file_candidates(hits: list[dict], *, limit: int) -> list[dict]:
    """The distinct files *hits* resolve to, best first, one ``{path}`` each.

    Ranked-order dedup: two symbol pages in one file are one file to open, and
    a symbol page whose own file page also ranked is not a second place to
    look. Pages that name no file are skipped rather than emitted as a path,
    which is what keeps the block's promise that every entry can be opened.

    Nothing is fetched to build this. It reads the hits a search already has.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for hit in hits:
        path = hit_file_path(hit)
        if not path or path in seen:
            continue
        seen.add(path)
        out.append({"path": path})
        if len(out) >= limit:
            break
    return out
