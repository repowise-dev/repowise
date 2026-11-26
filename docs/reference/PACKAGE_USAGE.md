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

`repowise packages vulns` lists each stored advisory the version in use is not proven clear of, with one tier, strongest first:

| Tier | Meaning |
|---|---|
| `package_called` | A function production code reaches makes a proven call into the package, at the call site shown. Python code. |
| `imported` | Shipping code imports the package; no call into it is claimed. JavaScript, TypeScript and Go stop here. |
| `unknown` | No shipping import was found. The package may still come through another dependency, or under an import name the index does not link to it, so it ranks with `imported`, never below it. |

What each tier rests on:

- **Production roots:** entry points, files a framework loads by convention, the functions serving stored HTTP routes, and CLI commands, kept only in production code (`code_origin`). Reach walks the reliable call graph from them.
- **Calls into a package:** a call the index proves is bound to the package by an import in the caller's file (`from x import y; y()`, `import x; x.y()`). Method calls on a value of a package type are not recorded, so their absence keeps an advisory at `imported`. An index built before call recording reads as `imported` until the next `repowise update` with a change.
- **No discounting:** Repowise never says a vulnerable package is unused or test-only, and makes no claim about which function inside a package an advisory affects.

`--fail-on TIER` (default `package_called`) exits 1 when an advisory sits at that tier or stronger, for CI (`imported` also counts `unknown`); `--format sarif` writes one result per advisory at its first site, for code scanning.

`repowise packages check pypi:requests@2.31.0` answers from the stored document before a package is added: `has_advisories` (with the fix above that version), `ok` (the document names the package and no advisory covers the version), or `unknown_no_source` (no document, or it does not name the package). Repowise does not look the package up anywhere; an `advisory-source` plugin can supply a feed.

Agents get the same answers. `get_context(targets=["package:pypi:requests"])` adds `advisories` (up to 5: id, tier, severity, fix, sites) to the card, and `package:pypi:requests@2.31.0` adds a `check`. `get_risk` adds one `advisory_call` line to a file holding a `package_called` call site. A package or file with no stored advisory gets neither.
