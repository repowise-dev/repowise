# Package usage

`repowise packages where <name>` lists the files that import a third-party package, with the import line, the component each file belongs to, its declared owner teams, and whether it is reachable. Packages come from the manifests the index already parses (`package.json`, `pyproject.toml`, `requirements*.txt`, `Cargo.toml`, `go.mod`, `.csproj`, `pom.xml`); import sites come from the import graph. The same answer is served by `get_context(targets=["package:<name>"])` and `GET /api/repos/{id}/external-systems/{package}/usage`.

## What each field rests on

| Field | Basis |
|---|---|
| `mapping: declared` | Indexing linked the import to the package's manifest declaration: the module is the declared name or starts with it (`requests.adapters`, `lodash/merge`), the longest declared module path wins for a Go package (`google.golang.org/protobuf/encoding/protojson` under `google.golang.org/protobuf`, never `/v2` paths under the module without `/v2`), and a PyPI distribution also answers to the module it is imported under (`Pillow` as `PIL`, `djangorestframework` as `rest_framework`, `Django` as `django`). Each entry in `modules` carries its own mapping, and a file's symbols and line come from its declared modules when it has any. |
| `mapping: inferred` | No declaration is linked; the module merely starts with the name asked for, or, for a PyPI distribution, with the module it is imported under (asking for `pillow` finds `PIL`). |
| `line` | The first import statement in the file that names the module: a line that starts with an import keyword (comments never count), or a line inside a Go `import ( ... )` block. Absent when none matches. |
| `owners` | Declared teams only: the catalog `spec.owner` and CODEOWNERS teams. Individual owners, and bare `@name` handles that may be a person or a GitLab group (`unresolved`), are counted in `owner_counts` and never named. |
| `reachability` | `reachable`: an entry point, a file a framework loads by convention, or a file holding a stored HTTP route's handler is the file or loads it through stored import edges or the dynamic loads indexing resolves (`importlib.import_module`, `"module:attr"` string tables such as lazily loaded CLI commands). `unreachable`: an open `unreachable_file` finding that the dead-code surface serves. `test`: a test file. `unknown`: neither, since a framework, script or reflection may still load it. |

## Export and import

`repowise packages export --format cyclonedx` writes a CycloneDX 1.6 JSON document: one `library` component per declared package (spellings that name the same package, such as `PyYAML` and `pyyaml`, merge into one), with `evidence.occurrences` (`location`, `line`, `symbol`, `additionalContext`) for each declared import site. It records where packages are used. It is not an SBOM: there are no hashes or licenses, no transitive dependencies, and no resolved versions; the manifest's version specifier is kept as the `repowise:declared_version` property.

`repowise packages import <file>` reads a Syft JSON, SPDX 2.x JSON, CycloneDX JSON or OSV JSON file you supply (a record, a list of records, or OSV-Scanner output) and joins its packages to usage by name, and by ecosystem when the file gives one. Repowise never fetches advisory feeds. Each advisory keeps its severity, the affected symbols the record names, and its fixed versions. OSV-Scanner output is already matched to resolved versions; for raw OSV records, the affected ranges are checked against each version the manifests pin exactly (PEP 440 for PyPI, SemVer for npm and Cargo, module versions for Go), and an advisory stays undecided (`affected: null`) when the manifest declares a range. The document is stored as the repository's advisory data, replacing the previous import. The reader is the local default of the `advisory-source` seam (`REPOWISE_SEAM_ADVISORY_SOURCE`); a plugin can register another source, which receives the repository's declared packages.

## Advisories and tiers

`repowise packages vulns` lists each stored advisory the version in use is not proven clear of, with one tier: the closest production code comes to it. Tiers, strongest first:

| Tier | Meaning |
|---|---|
| `reachable` | The advisory names affected symbols, and a function production code reaches calls one. Go is judged by import path: a reached file imports an affected package path. |
| `package_called` | A function production code reaches makes a proven call into the package, but no call to a named affected symbol is proven, or the advisory names none (PyPI and npm advisories rarely do). |
| `imported` | Shipping code imports the package, and no reachable call into it is proven. |
| `indirect_unknown` | No shipping code imports it, but it is, or may be, pulled in by another dependency, whose calls into it the index cannot see. |
| `test_or_dev_only` | A direct-only dependency imported only by test, tooling, build or docs-example code. |
| `not_imported` | A direct-only dependency nothing imports. |

What each tier rests on:

- **Production roots:** entry points, files a framework loads by convention, and the functions serving stored HTTP routes, kept only in production code (`code_origin`). Reach walks the reliable call graph from them.
- **Calls into a package:** a call the index proves is bound to the package by an import in the caller's file (`from x import y; y()`, `import x; x.y()`, `import {y} from 'x'`, `require('x').y()`), recorded for Python, JavaScript and TypeScript. Method calls on a value of a package type are not recorded, so their absence keeps an advisory at `imported`. An index built before call recording reads as `imported` until the next `repowise update` with a change.
- **Never buried:** nothing goes below `imported` without proof, and a package that may be transitive stays at `indirect_unknown`. A document that marks a package direct does not rule out another dependency requiring it, so `test_or_dev_only` and `not_imported` need a dependency graph that shows it is direct only.

`--fail-on TIER` (default `package_called`) exits 1 when an advisory sits at that tier or stronger, for CI; `--format sarif` writes one result per advisory at its first site, for code scanning.

`repowise packages check pypi:requests@2.31.0` answers from the stored document before a package is added: `has_advisories` (with the fix above that version), `ok` (the document names the package and no advisory covers the version), or `unknown_no_source` (no document, or it does not name the package). Repowise does not look the package up anywhere; an `advisory-source` plugin can supply a feed.

Agents get the same answers. `get_context(targets=["package:pypi:requests"])` adds `advisories` (up to 5: id, tier, severity, fix, sites) to the card, and `package:pypi:requests@2.31.0` adds a `check`. `get_risk` adds one `advisory_call` line to a file whose call production code reaches into a package with an advisory (tier `reachable` or `package_called`). A package or file with no stored advisory gets neither.
