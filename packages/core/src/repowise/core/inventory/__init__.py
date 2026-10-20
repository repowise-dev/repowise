"""Inventory: where third-party packages are used, and what each component is made of.

:mod:`.usage` answers "where is package X used" from the import graph;
:mod:`.cyclonedx` exports that as CycloneDX 1.6 ``evidence.occurrences``;
:mod:`.advisories` reads a user-supplied Syft, SPDX, CycloneDX or OSV file
(the ``advisory-source`` seam) and joins it to usage by name;
:mod:`.components` builds the component inventory document.
"""
