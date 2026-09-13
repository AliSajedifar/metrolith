# JavaScript/JSX compatibility fallback

ArchLens first parses the original repository bytes with the grammar selected
for the file extension. A primary parse is complete only when it contains zero
Tree-sitter `ERROR` nodes and zero missing nodes. Complete primary parses are
used unchanged and do not attempt a fallback.

For malformed `.js`, `.jsx`, `.mjs`, or `.cjs` files, ArchLens copies the bytes
to an in-memory buffer and applies these deterministic stages in order:

1. `javascript_import_assertion_compat` replaces `assert` with the same-width
   `with  ` only in JSON `import` statements whose assertion declares
   `type: "json"`.
2. `jsx_reserved_attribute_compat` replaces the first byte of a reserved word
   with `_` only when a conservative JSX tag scanner identifies the word as an
   attribute name. Tag names, `className`, JavaScript expressions, strings,
   comments, templates, and regular expressions are not rewritten.
3. `raw_jsx_ampersand_compat` replaces a raw `&` with one parser-only space
   byte only when an `ERROR` node begins at that byte in JSX text or a quoted
   JSX attribute. Entities, expressions, strings, and comments are unchanged.

Malformed TypeScript and TSX use a separate bounded sequence. The raw JSX
ampersand rule is available to TSX, and
`typescript_keyword_parameter_compat` replaces the first byte of bare
`any`, `boolean`, or `string` parameter names in malformed function-type
signatures such as `(any) => void`. Runtime arrows and typed parameters with a
colon are not candidates.

Typed `.js` files are considered for `typed_javascript_tsx_compat` only when a
parameter-annotation signature is malformed and the repository contains
explicit Flow/Babel evidence such as `.flowconfig`, `flow-bin`,
`babel-eslint`, or a Flow Babel preset. The detected language remains
JavaScript and diagnostics record TSX as the fallback grammar. Without that
evidence, the file remains Partial as `unsupported_javascript_dialect`.

The buffer is reparsed after every stage that changes it. Multiple strategies
are recorded in application order. No buffer is written to disk.

When a candidate is clean, entity counts come from that AST and the final file
status is Complete. Otherwise ArchLens compares the primary and fallback
candidates by error-node count, missing-node count, successfully parsed byte
coverage, and attempt order. A fallback is selected only when neither malformed
count worsens and at least one strictly improves; primary wins all other cases.
Counts from a malformed
winner are retained only as lower bounds and the final file status remains
Partial. Diagnostics always retain the original source positions; recovered
files are written to `recoveries.csv`, while unresolved files remain one row per
file in `errors.csv`.

Navigation: [usage](USAGE.md) · [project README](../README.md).
