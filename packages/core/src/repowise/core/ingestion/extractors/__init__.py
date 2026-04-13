"""Per-language extractor modules — bindings, heritage, docstrings, signatures, visibility."""

from .bindings import extract_import_bindings
from .docstrings import extract_module_docstring, extract_symbol_docstring
from .helpers import extract_go_receiver_type, node_text, refine_go_type_kind
from .heritage import HERITAGE_EXTRACTORS, extract_heritage
from .signatures import build_signature
from .visibility import VISIBILITY_FNS

__all__ = [
    "HERITAGE_EXTRACTORS",
    "VISIBILITY_FNS",
    "build_signature",
    "extract_go_receiver_type",
    "extract_heritage",
    "extract_import_bindings",
    "extract_module_docstring",
    "extract_symbol_docstring",
    "node_text",
    "refine_go_type_kind",
]
