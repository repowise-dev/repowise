# Package usage

`repowise packages where <name>` lists the files that import a third-party package, with the import line, the component each file belongs to, its declared owner teams, and whether it is reachable. Packages come from the manifests the index already parses (`package.json`, `pyproject.toml`, `requirements*.txt`, `Cargo.toml`, `go.mod`, `.csproj`, `pom.xml`); import sites come from the import graph. The same answer is served by `get_context(targets=["package:<name>"])` and `GET /api/repos/{id}/external-systems/{package}/usage`.

## What each field rests on

| Field | Basis |
|---|---|
| `mapping: declared` | Indexing linked the import to the package's manifest declaration: the module is the declared name or starts with it (`requests.adapters`, `lodash/merge`). Each entry in `modules` carries its own mapping, and a file's symbols and line come from its declared modules when it has any. |
| `mapping: inferred` | No declaration is linked; the module merely starts with the name asked for. A Python distribution imported under another name (`PyYAML` as `yaml`) is found only by asking for the import name. |
| `line` | The first import statement in the file that names the module: a line that starts with an import keyword (comments never count), or a line inside a Go `import ( ... )` block. Absent when none matches. |
| `owners` | Declared teams only: the catalog `spec.owner` and CODEOWNERS teams. Individual owners, and bare `@name` handles that may be a person or a GitLab group (`unresolved`), are counted in `owner_counts` and never named. |
| `reachability` | `reachable`: an entry point loads the file through stored import edges or the dynamic loads indexing resolves (`importlib.import_module`, `"module:attr"` string tables such as lazily loaded CLI commands). `unreachable`: an open `unreachable_file` finding that the dead-code surface serves. `test`: a test file. `unknown`: neither, since a framework, script or reflection may still load it. |

## Export and import

`repowise packages export --format cyclonedx` writes a CycloneDX 1.6 JSON document: one `library` component per declared package (spellings that name the same package, such as `PyYAML` and `pyyaml`, merge into one), with `evidence.occurrences` (`location`, `line`, `symbol`, `additionalContext`) for each declared import site. It records where packages are used. It is not an SBOM: there are no hashes or licenses, no transitive dependencies, and no resolved versions; the manifest's version specifier is kept as the `repowise:declared_version` property.

`repowise packages import <file>` reads a Syft JSON, SPDX 2.x JSON, CycloneDX JSON or OSV JSON file you supply (a record, a list of records, or OSV-Scanner output) and joins its packages to usage by name, and by ecosystem when the file gives one. Repowise never fetches advisory feeds and never matches vulnerabilities itself: an OSV file's own affected-package list is joined by name and its version ranges are not evaluated. The reader is the local default of the `advisory-source` seam (`REPOWISE_SEAM_ADVISORY_SOURCE`); a plugin can register another source, which receives the repository's declared packages.
