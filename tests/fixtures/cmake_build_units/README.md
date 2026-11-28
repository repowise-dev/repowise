# cmake_build_units

A buildable CMake monorepo shaped like issue #5775, for the C/C++ build-units adapter.

- Modules `A`, `B`, `C`, each with components `1`–`3`, plus the shared library `A/ACommon`.
- Vertical slices: `An -> Bn -> Cn`. `A1`–`A3` all link `ACommon`.
- `A2` has five sources, more than any other component. That makes it the
  largest member of the merged cluster that community detection produces, so
  the cluster gets A2's name.
- `C1` links `Threads::Threads`, a third-party target that must not become a
  unit or a dep.
- `app` is an executable linking `A1`–`A3`.
- The module-level `A/`, `B/` and `C/CMakeLists.txt` files only call
  `add_subdirectory` and declare no target.

Expected build units (ids depend on the adapter's naming):

| Unit | Root | Deps |
|---|---|---|
| ACommon | `A/ACommon` | none |
| A1 | `A/A1` | ACommon, B1 |
| A2 | `A/A2` | ACommon, B2 |
| A3 | `A/A3` | ACommon, B3 |
| B1, B2, B3 | `B/Bn` | Cn |
| C1, C2, C3 | `C/Cn` | none (Threads::Threads dropped) |
| app | `app` | A1, A2, A3 |

The CMake target graph has no `A2 -> B1` edge.

Check it with the build tool itself:

    cmake -S tests/fixtures/cmake_build_units -B <build-dir> && cmake --build <build-dir>
