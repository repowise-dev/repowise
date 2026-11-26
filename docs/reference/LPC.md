# LPC (LDMud): experimental language support

LPC shares `.c` and `.h` suffixes with C. Repowise treats these as LPC only
under explicitly configured mudlib roots. Driver sources outside those roots
keep their existing C/C++ classification. There is no content-based guessing.
This initial integration targets LDMud syntax; FluffOS compatibility is not claimed.

## Install the optional grammar

LPC needs the MIT-licensed [tree-sitter-lpc](https://github.com/repowise-dev/tree-sitter-lpc)
grammar, published on PyPI with wheels for Linux, macOS and Windows. Install it with the `lpc` extra:

```sh
pip install "repowise[lpc]"
# from a source checkout:
uv sync --all-packages --extra lpc
```

Without the grammar, LPC stays inactive: `.c`/`.h` files under configured mudlib roots keep their
C/C++ classification and repowise logs one warning naming the extra. Nothing is downloaded at runtime.
Grammar fixes go to the [fork](https://github.com/repowise-dev/tree-sitter-lpc).

## Configure the mudlib

Create a JSON file, for example `lpc-index.json`:

```json
{
  "roots": ["lib"],
  "include": ["sys", "sys/driver", ""]
}
```

Set `REPOWISE_LPC_CONFIG` to its path for both indexing and serving. Root paths
are relative to that file; include directories are relative to each mudlib root.
Alternatively set `REPOWISE_LPC_ROOTS` to a platform-path-separator-delimited
list of absolute roots. This adds roots to any specified by the config file.
Only list mudlib directories, not a parent containing both the mudlib and driver.
The most specific root wins when roots nest. Separate roots are not implicitly
overlaid. Symlinks escaping the configured root are not classified as LPC or
accepted as import targets. Invalid/missing config files are reported as errors.

```sh
export REPOWISE_LPC_CONFIG="$PWD/lpc-index.json"
repowise init ./lib --no-prose --no-editor-setup
```

Mudlibs sometimes use `vendor/` for game content. Repowise's existing directory
filters still apply; explicitly re-include it in `.repowiseIgnore` when needed:

```gitignore
!**/vendor/
```

## Supported in this contribution

- Functions with bodies, globals, structs, object-like and function-like macros.
  Function prototypes are excluded. `private`, `protected`, and `static`
  visibility are preserved.
- Literal root-absolute `inherit "/std/room";` and literal `#include` statements.
  Quoted includes search the importing directory first; angle includes use the
  configured include directories. Resolved targets must belong to the index.
- Bare and member call sites, named callback strings in common efuns, and named
  lfun closures. These are syntactic captures, not proof of receiver identity.
- The import capability is explicitly `partial`. LPC has no language-specific
  health, heritage, receiver typing or inherited-dispatch implementation here.

## Deliberate limits

Macro-based or concatenated import paths, relative inherits, global driver
includes, preprocessor branch evaluation, and master-hook path rewriting are
not resolved. Import syntax in inactive branches may still be captured: this
is syntax analysis, not the compiler's effective program. Scoped calls such as
`::create()` and `"/std/room"::create()` await an audited LPC call strategy.
Runtime `clone_object`/`call_other` paths and arbitrary callback registries are
not modeled. General call resolution remains approximate.

The driver invokes applies and loads objects by runtime names. Because this
initial graph omits those entry paths, **LPC is exempt from file- and symbol-level
dead-code claims**. An absent edge must not be treated as deletion evidence.

## Validation

```sh
uv run pytest tests/unit/ingestion/test_lpc.py
uv run pytest tests/providers/ tests/unit/
uv run ruff check .
```

The fixtures are synthetic and contain no private mudlib code. Tests cover
mixed C/LPC routing, parser symbols and callbacks, imports through the real
traverser/parser/graph pipeline, malformed config, nested roots, and symlink
escapes. Broader macro and dispatch support should arrive as separate changes
with driver-mode configuration and precision evidence.
