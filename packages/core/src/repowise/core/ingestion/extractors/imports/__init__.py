"""Per-language import extraction, one ``ImportExtractor`` per language.

Each language's rules live in ``<lang>.py``; ``LanguageConfig.import_extractor``
wires them in, and a language without one takes ``GENERIC_IMPORTS``. This
package never imports the parser, so ``language_configs`` can import it.
"""

from ._types import ImportExtractor
from .dart import DART_IMPORTS
from .elixir import ELIXIR_IMPORTS
from .fsharp import FSHARP_IMPORTS
from .generic import GENERIC_IMPORTS
from .jvm import JVM_IMPORTS
from .pascal import PASCAL_IMPORTS
from .php import PHP_IMPORTS
from .python import PYTHON_IMPORTS
from .rust import RUST_IMPORTS
from .scala import SCALA_IMPORTS
from .swift import SWIFT_IMPORTS
from .ts_js import TS_JS_IMPORTS

__all__ = [
    "DART_IMPORTS",
    "ELIXIR_IMPORTS",
    "FSHARP_IMPORTS",
    "GENERIC_IMPORTS",
    "JVM_IMPORTS",
    "PASCAL_IMPORTS",
    "PHP_IMPORTS",
    "PYTHON_IMPORTS",
    "RUST_IMPORTS",
    "SCALA_IMPORTS",
    "SWIFT_IMPORTS",
    "TS_JS_IMPORTS",
    "ImportExtractor",
]
