# Structural Template Localization Design

## Goal

Make every deterministic structural wiki page use the configured output language for fixed labels and prose, while preserving source-code identifiers, paths, symbols, and an English fallback.

## Scope

The change covers `file_page`, `symbol_spotlight`, `layer_page`, `infra_page`, `scc_page`, and `api_contract`. These are all rendered by `StructuralRenderMixin._structural_page` with no model involved, so the language instruction used by LLM pages cannot affect them.

## Design

Add a dependency-free structural-label catalog keyed by language code. English is the complete default catalog; German supplies translated values for the labels and prose used in all six templates. Resolving an unsupported or unmapped language returns English, never a partial mapping or an undefined Jinja value.

`_render_page` passes the resolved catalog as `labels` to each Jinja render. Templates replace their fixed English headings, metadata names, empty-state messages, and connective prose with catalog lookups. Dynamic values remain untouched: code paths, symbol names, signatures, language identifiers, extracted docstrings, and parsed source are rendered exactly as before.

Because structural page fingerprints already include the active language and template source, localized template changes naturally cause `update` to regenerate stale structural pages.

## Error Handling

The catalog resolver accepts `str | None` and always returns a complete English-backed mapping. Templates only index keys guaranteed by that mapping, keeping the existing `StrictUndefined` safety property.

## Verification

Add rendering tests that configure German and assert localized headings across all six structural page types. Add an unknown-language test that asserts the exact English fallback, and retain the existing English rendering tests. Run the deterministic-template suite plus the structural page generator tests and lint the changed Python and template-adjacent test files.
