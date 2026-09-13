# ARCH-Bench Metric Contract 2.0.0 (legacy pre-rename contract)

Historical compatibility reference. Current measurements use [Metric Contract 3.0.0](METRIC_CONTRACT_V3.md).

Historical contract: superseded by [METRIC_CONTRACT_V3](METRIC_CONTRACT_V3.md) for new runs.

This document is normative for the four benchmark columns. `architecture_type`
never changes these definitions.

## Scope

Count first-party production `.java`, `.js`, `.jsx`, `.mjs`, `.cjs`, `.ts`,
`.tsx`, `.mts`, `.cts`, `.py`, and `.go` files selected by exclusion policy
1.2.0. Unsupported, test, fixture, generated, vendor, dependency, build,
minified/bundled, declaration-only, stub, and oversized files do not contribute.

Policy 1.2.0 recognizes conventional test modules, test-runner configs,
test-suffixed files, Go test helpers, and prefixed test roots,
static resource library trees, and templated JavaScript/TypeScript source.
Names alone do not decide scope when they are also ordinary domain vocabulary:
for example, a `vendors` feature below an application `src` domain container is
not treated as a third-party vendor tree.

## Source Files

One per included file. Read/entity parse failure does not erase the file count.

## Lines of Code

`lines_of_code = code_lines`: physical lines with syntactic code. Blank and
comment-only lines are excluded; code with an inline comment counts once.
Syntactic strings and Python docstrings count as code. This is not logical
statement count.

## Classes / Structs

`classes_structs = classes + records + structs`, where the main categories are
Java named classes/records, JavaScript/TypeScript named class declarations,
Python classes, and Go named struct types. Interfaces, enums, annotation types,
aliases, and anonymous entities are secondary only.

## Methods / Functions

`methods_functions = module_functions + class_methods + receiver_methods`.
Count named implementations at module/package scope and direct members of
classes, named JS/TS objects, or Go receiver types. Exclude constructors,
anonymous callbacks/functions without a stable assignment, lambdas, nested
local functions, declaration/abstract/interface signatures, overload
signatures, annotation members, and generated methods.

## Aggregation and status

Keep Java, JavaScript, TypeScript, Python, and Go separate and also sum them.
Primary language comes from explicit `expected_language`, otherwise maximum
`code_lines`. Status is `complete`, `partial`, `failed`, or `not_applicable`.
Failures are never labeled as verified zero.

Navigation: [usage](USAGE.md) · [project README](../README.md).
