# ArchLens Metric Contract 3.0.0

This document is normative for the four benchmark metrics. `architecture_type`
never changes these definitions. The original context was Exclusion Policy
1.5.0, Inventory Schema 1.6.0 and Artifact Schema 1.4.0. These metric definitions
remain current in Metrolith 4.0.0 with Inventory 1.7.0 and Artifact 1.12.0;
see [current versions](USAGE.md#command-and-component-reference). Later schema
changes do not redefine these metrics.

## Scope and Source Files

Source Files is the number of recognized first-party production `.java`, `.js`,
`.jsx`, `.mjs`, `.cjs`, `.ts`, `.tsx`, `.mts`, `.cts`, `.py`, and `.go` files
after global path, test, generated, vendor/dependency, build-output,
declaration/stub, and bundle exclusions. Each included path counts once.

Successful reading or parsing is not a prerequisite. An unreadable, oversized,
invalid-syntax, or parser-failed source remains in Source Files. The failure
changes LOC/entity statuses and supporting read/parse counts rather than
silently reducing Source Files.

For repository-wide output, JavaScript means the JavaScript/TypeScript family:
`.js`, `.jsx`, `.mjs`, and `.cjs` use the JavaScript grammar; `.ts`, `.mts`,
and `.cts` use the TypeScript grammar; `.tsx` uses the TSX grammar. All eight
extensions are inventoried before per-language aggregation.

Policy 1.5.0 prunes unambiguous excluded subtrees before descent, preserves the
domain `src/.../vendors` exception, uses `*_test.go` plus path/name evidence for
Go tests, matches Java test suffixes case-insensitively, and accepts generated
markers only in an initial language comment/header region. It also excludes
high-confidence Qt Linguist XML catalogs named `.ts` as
`content_type_mismatch` and dialect-corroborated mixed templates as
`templated_source`. Ambiguous single markers do not trigger these rules.

## Lines of Code

`lines_of_code = code_lines`: physical lines containing syntactic code. Blank
and comment-only lines are excluded; code plus an inline comment counts once.
Syntactic strings and Python docstrings count as code. Python encoding follows
`tokenize.detect_encoding`, including UTF-8 BOM and PEP 263 cookies. Invalid or
undecodable Python is not replacement-decoded and labeled complete.
BOM-identified UTF-16LE and UTF-16BE are decoded strictly, measured from the
decoded logical source, and converted to UTF-8 only for parser input. Original
Git bytes and hashes remain unchanged. Invalid UTF-16 has failed LOC status.
Parser-view offsets are retained, while original-byte offsets are null when a
reliable mapping through the UTF-16 transformation is unavailable.
Strict UTF-8 JavaScript/TypeScript containing low-density NUL bytes only inside
quoted strings, template literals, or comments may use a same-length,
parser-only space substitution. High-density, alternating, binary, invalid
UTF-8, and unrecognized-context NUL inputs remain failed encoding evidence.

## Classes / Structs

`classes_structs = classes + records + structs`. The main categories are:

- Java named classes and records, including named nested/local classes;
- JavaScript/TypeScript named class declarations and stable module-level class
  assignments such as `const C = class {}`, `module.exports = class {}`,
  `module.exports.C = class {}`, and `exports.C = class {}`;
- Python classes, including classes defined inside functions;
- Go named struct types.

Interfaces, enums, annotation types, aliases, anonymous classes, and anonymous
structs are secondary. Anonymous-class methods do not contribute to the main
Methods / Functions total.

## Methods / Functions

`methods_functions = module_functions + class_methods + receiver_methods`.
Count implementation-bearing module/package functions and direct methods of
counted named classes, named JavaScript/TypeScript objects, and Go receiver
types. Stable module-scope CommonJS functions assigned to `module.exports`,
`module.exports.name`, or `exports.name` count. Dynamic/computed assignments,
call-result assignments, nested CommonJS assignments, anonymous callbacks,
lambdas, nested functions, and signature-only declarations do not count.

**Clarification (3.5.0 documentation only; no definition changed).** The
stable-assignment rule stated above for Classes / Structs applies symmetrically
to functions, and always has. A **stable named module-scope binding** such as
`const f = (value) => value * 2` or `const f = function (value) { ... }` is an
implementation-bearing module function and counts. The exclusion of "lambdas"
and "anonymous callbacks" concerns **anonymous** forms, such as a function
expression passed directly as an argument. The distinction is the stable name at
module scope, not the arrow or `function` syntax.

This paragraph records behaviour that Metric Contract 3.0.0 already specifies
through the Classes / Structs wording; it is written out here because the
function case was previously left to inference. Metric Contract remains 3.0.0
and no measured value changes. Conformance cases
`entity-js-arrow-lambda-is-secondary` (positive) and
`entity-js-anonymous-callback-is-secondary` (negative) pin both sides.

Direct methods of a Python class inside a function are class methods; a function
nested inside such a method remains nested. Ordinary Java constructors, Java
compact record constructors, Python `__init__`, and Python `__new__` remain
secondary constructors and are excluded from Methods / Functions.

## Malformed ASTs

Tree-sitter `ERROR` and `MISSING` ranges are recorded once per affected file
with grammar identity, bounded node detail, line/column/byte ranges, escaped
preview, affected metrics, and normalized cause. An entity is rejected when its name or
required declaration/body structure is missing or when it lies in an unreliable
malformed subtree. Valid observed declarations outside malformed ranges may be
retained with a partial entity status. LOC status is independent. A partial
numeric zero is not a verified complete zero.

## Per-metric status

Every aggregate and language row carries `inventory_status`,
`source_files_status`, `loc_status`, `classes_structs_status`, and
`methods_functions_status`, using `complete`, `partial`, `failed`, or
`not_applicable`. Historical `metric_status` is the conservative worst status.

Supporting fields are `source_files_readable`, `source_files_loc_analyzed`,
`source_files_entity_parsed`, `source_files_failed_read`,
`source_files_oversized`, `source_files_partial_parse`, and
`source_files_failed_parse`. For each language and aggregate,
`source_files = source_files_readable + source_files_failed_read +
source_files_oversized`. Partial observed
values may be useful, but must not be interpreted as verified complete values.

## Cross-language comparability limitations

The four metrics share names and aggregation rules, but their raw entity counts
are not perfectly measurement-equivalent across languages. Java, TypeScript,
JavaScript, and Go interfaces are not included in the main Classes / Structs
metric. Go counts named module-scope structs. Python counts class declarations
including nested and function-local classes. Java local classes are represented
through Java grammar declaration forms that do not map one-for-one to Python's
scope behavior. JavaScript and TypeScript count only stable named class forms;
unstable, computed, and anonymous class forms have deliberately restricted
treatment.

Appropriate uses include repository characterization, within-language
comparison, parser-coverage assessment, and controlled sensitivity analysis.
Raw counts alone must not be interpreted as cross-language architecture-quality
measures.

## Multi-language primary view

All supported languages remain separate and are also aggregated.
`expected_language` selects the primary view only when it has an included source
file. Otherwise `expected_language_mismatch=true` and observed metrics select by
highest LOC, then Source Files, then Java, JavaScript, TypeScript, Python, Go.
An exact top LOC/Source Files tie sets `primary_language_tie=true`.

## Versioned semantic changes

Relative to 2.0.0, intentional metric changes are CommonJS exported functions,
stable assigned class expressions, and direct methods of named local classes.
Policy 1.5.0 can also correct included paths for content-type and templated
source rules, while retaining the earlier generated-header, Go test, and
case-insensitive Java test rules. Targeted Git acquisition, retry/timeout,
pruning, decoded-text caching, logging, and Markdown null formatting are
engineering-only and must not change metrics for otherwise identical included
source.

Repository-specific Declared Analysis Scope remains unimplemented. Dynamic and
runtime-generated behavior remains outside the static contract.
