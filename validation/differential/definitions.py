"""Metric definition mappings for differential validation.

A numeric disagreement means nothing until both sides' definitions are written
down. Two tools can be individually correct and still disagree by 30% because
one counts a brace line and the other does not.

Every mapping here records, for one (metric, language, reference) triple:

* the unit counted;
* how comments and blank lines are treated;
* language-specific construct treatment;
* how generated / vendored / test code is treated;
* the parser or scanning technology on each side;
* known definition mismatches that are expected and must not be read as
  defects.

**The reference implements ArchLens's documented definition, independently.**
That is deliberate. A reference implementing a *different* definition would
make every comparison a definition mismatch and would test nothing. Holding the
definition fixed and varying the implementation is what turns a disagreement
into evidence about one of the two implementations.

**No reference here is ground truth.** A disagreement is a finding about both
sides until adjudicated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DefinitionMapping:
    """One explicitly mapped metric definition."""

    identifier: str
    metric: str
    languages: tuple[str, ...]
    unit_counted: str
    comment_treatment: str
    blank_treatment: str
    construct_treatment: str
    selection_treatment: str
    archlens_mechanism: str
    reference_mechanism: str
    independent: bool
    known_mismatches: tuple[str, ...] = ()
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "definition_mapping_id": self.identifier,
            "metric": self.metric,
            "languages": list(self.languages),
            "unit_counted": self.unit_counted,
            "comment_treatment": self.comment_treatment,
            "blank_treatment": self.blank_treatment,
            "construct_treatment": self.construct_treatment,
            "selection_treatment": self.selection_treatment,
            "archlens_mechanism": self.archlens_mechanism,
            "reference_mechanism": self.reference_mechanism,
            "reference_is_independent": self.independent,
            "known_definition_mismatches": list(self.known_mismatches),
            "notes": self.notes,
        }


ALL_LANGUAGES = ("Python", "Java", "JavaScript", "TypeScript", "Go")

#: `lines_of_code`, every supported language.
#:
#: ArchLens masks comments and then classifies each physical line. A line
#: carrying both code and a trailing comment is CODE, because the masked line
#: still holds non-whitespace. A Python docstring is CODE, not a comment: it is
#: a string expression and `tokenize` only reports COMMENT tokens. That last
#: point is where most third-party counters would disagree, so the reference
#: adopts ArchLens's definition and varies only the implementation.
LOC_LINE_CLASSIFICATION = DefinitionMapping(
    identifier="loc.physical_code_lines.v1",
    metric="lines_of_code",
    languages=ALL_LANGUAGES,
    unit_counted=(
        "physical source lines whose content is not exclusively a comment, "
        "counted after comment masking"
    ),
    comment_treatment=(
        "comment-only lines excluded; a line mixing code and a comment counts "
        "as code; comment markers inside string literals are not comments"
    ),
    blank_treatment="whitespace-only physical lines excluded",
    construct_treatment=(
        "Python docstrings count as CODE (string expressions, not comments). "
        "Go raw string literals and JavaScript template literals are string "
        "content, so comment markers inside them are not comments."
    ),
    selection_treatment=(
        "Track A: the file set is fixed by ArchLens, so selection cannot "
        "contribute to any disagreement."
    ),
    archlens_mechanism=(
        "Python: stdlib `tokenize` COMMENT tokens. Java/JavaScript/TypeScript/"
        "Go: tree-sitter comment nodes."
    ),
    reference_mechanism=(
        "clean-room character-level scanner with per-language string and "
        "comment states; no tokenizer, no parser, no ArchLens code"
    ),
    independent=True,
    known_mismatches=(
        "A line continued inside a multi-line string is code on both sides; "
        "counters that treat a whole triple-quoted block as a comment would "
        "disagree with both.",
    ),
    notes=(
        "The reference is independent for every language, including Python: "
        "ArchLens uses stdlib `tokenize` and the reference uses none."
    ),
)

#: `source_files`, every supported language. Track A only — under Track A the
#: file set is given, so this validates the count, not the selection. Track B
#: validates the selection itself.
SOURCE_FILE_COUNT = DefinitionMapping(
    identifier="source_files.counted_set.v1",
    metric="source_files",
    languages=ALL_LANGUAGES,
    unit_counted="files ArchLens included in metrics",
    comment_treatment="not applicable",
    blank_treatment="not applicable",
    construct_treatment=(
        "an empty but included source file still counts as one source file"
    ),
    selection_treatment=(
        "Track A holds the set fixed; disagreement here would indicate an "
        "adapter or accounting defect, not a selection difference"
    ),
    archlens_mechanism="count of inventory records with included_in_metrics",
    reference_mechanism="count of files in the supplied set the reference read",
    independent=True,
    notes=(
        "Deliberately weak as a metric test. It exists to detect adapter "
        "drift: if the two sides disagree on how many files they looked at, "
        "no other Track A comparison in that case is trustworthy."
    ),
)

#: Java entity counts. The only entity metrics with an independent parser
#: available in the pinned offline environment.
#: Verified against `core_metrics.derive_classes_structs`, which sums
#: `classes + records + structs` only. Interfaces, enums and annotation types
#: are tracked as separate components and deliberately excluded from this
#: metric. The first draft of this mapping asserted the opposite and produced a
#: 2-vs-4 "disagreement" that was entirely the mapping's fault — which is
#: precisely why an explicit, verified mapping precedes any comparison.
JAVA_TYPES = DefinitionMapping(
    identifier="classes_structs.java_class_and_record.v1",
    metric="classes_structs",
    languages=("Java",),
    unit_counted=(
        "named class and record declarations only, including nested and local "
        "ones"
    ),
    comment_treatment="not applicable",
    blank_treatment="not applicable",
    construct_treatment=(
        "interfaces, enums and annotation types are EXCLUDED on both sides: "
        "ArchLens counts them as separate components that do not feed "
        "classes_structs. Anonymous class bodies are excluded on both sides."
    ),
    selection_treatment="Track A: file set fixed by ArchLens",
    archlens_mechanism=(
        "tree-sitter-java 0.23.5; classes + records + structs components"
    ),
    reference_mechanism=(
        "JDK 17 javac Compiler Tree API (com.sun.source); ClassTree visits "
        "filtered to Kind.CLASS and Kind.RECORD"
    ),
    independent=True,
    known_mismatches=(
        "The reference also reports raw all_types / interfaces / enums / "
        "annotation_types so the size of the definitional exclusion stays "
        "visible as evidence rather than being silently dropped.",
    ),
    notes="javac is a reference front end, not ground truth.",
)

#: Verified against `core_metrics.derive_methods_functions`, which sums
#: `module_functions + class_methods + receiver_methods`. Constructors and
#: body-less declarations are tracked separately and excluded.
JAVA_METHODS = DefinitionMapping(
    identifier="methods_functions.java_concrete_named_owner.v1",
    metric="methods_functions",
    languages=("Java",),
    unit_counted=(
        "method declarations with a body, declared in a named type"
    ),
    comment_treatment="not applicable",
    blank_treatment="not applicable",
    construct_treatment=(
        "constructors are EXCLUDED on both sides (ArchLens counts them as a "
        "separate `constructors` component). Abstract and interface method "
        "declarations are EXCLUDED (ArchLens: `signature_only_methods`). "
        "Methods in anonymous class bodies are EXCLUDED (ArchLens: "
        "`anonymous_class_methods`). Lambdas are not methods on either side."
    ),
    selection_treatment="Track A: file set fixed by ArchLens",
    archlens_mechanism=(
        "tree-sitter-java 0.23.5; module_functions + class_methods + "
        "receiver_methods components"
    ),
    reference_mechanism=(
        "JDK 17 javac Compiler Tree API; MethodTree visits excluding <init>, "
        "excluding null bodies, excluding anonymous-class owners"
    ),
    independent=True,
    known_mismatches=(
        "javac synthesizes a default constructor during *attribution*; the "
        "reference stops at parse, so no synthetic member is ever counted.",
        "The reference also reports raw constructors / "
        "abstract_or_interface_methods / anonymous_class_methods as evidence.",
    ),
)

#: Track B source selection. No parser is involved, so this is available for
#: every language.
SOURCE_SELECTION = DefinitionMapping(
    identifier="selection.included_source_set.v1",
    metric="included_file_set",
    languages=ALL_LANGUAGES,
    unit_counted="repository-relative paths selected for measurement",
    comment_treatment="not applicable",
    blank_treatment="not applicable",
    construct_treatment=(
        "language detection by extension; test / vendor / generated / build "
        "output / dependency / minified classification"
    ),
    selection_treatment=(
        "Track B derives inclusion INDEPENDENTLY. The ArchLens file list is "
        "never supplied to the reference."
    ),
    archlens_mechanism="modules.inventory.RepositoryInventory + exclusion policy 1.5.0",
    reference_mechanism=(
        "clean-room filesystem walk with its own extension map and its own "
        "path-pattern classification; imports nothing from `modules`"
    ),
    independent=True,
    known_mismatches=(
        "The reference cannot reproduce content-based decisions such as "
        "generated-header detection or minification heuristics with the same "
        "fidelity; those disagreements are expected and are classified "
        "`metric_definition_mismatch` unless adjudicated otherwise.",
    ),
)

#: Python entities. Verified against `core_metrics._PythonEntityVisitor`:
#: every `ClassDef` at any depth is a class; `methods_functions` is
#: `module_functions + class_methods`, so `__init__`/`__new__` (constructors),
#: `@overload` declarations (signature_only_methods) and anything nested inside
#: a function or not a direct class-body member (nested_functions) are excluded.
PYTHON_TYPES = DefinitionMapping(
    identifier="classes_structs.python_classdef.v1",
    metric="classes_structs",
    languages=("Python",),
    unit_counted="every `class` statement, at any nesting depth",
    comment_treatment="not applicable",
    blank_treatment="not applicable",
    construct_treatment=(
        "nested and local classes ARE counted on both sides. Python has no "
        "record or struct construct, so classes_structs is exactly the class "
        "count."
    ),
    selection_treatment="Track A: file set fixed by ArchLens",
    archlens_mechanism="CPython stdlib `ast`, ClassDef visits",
    reference_mechanism="parso 0.8.7 concrete syntax tree, `classdef` nodes",
    independent=True,
    notes=(
        "The independence here is the whole point: ArchLens uses the stdlib "
        "parser, so a stdlib-based reference would share it. parso has its own "
        "grammar."
    ),
)

PYTHON_METHODS = DefinitionMapping(
    identifier="methods_functions.python_module_and_class.v1",
    metric="methods_functions",
    languages=("Python",),
    unit_counted=(
        "module-level function definitions plus direct class-body methods"
    ),
    comment_treatment="not applicable",
    blank_treatment="not applicable",
    construct_treatment=(
        "EXCLUDED on both sides: `__init__` and `__new__` (ArchLens: "
        "`constructors`); `@overload` declarations (`signature_only_methods`); "
        "functions nested inside another function, and functions inside a class "
        "that are not direct members of the class body, such as one defined "
        "inside an `if` (`nested_functions`). `async def` counts wherever the "
        "corresponding `def` would."
    ),
    selection_treatment="Track A: file set fixed by ArchLens",
    archlens_mechanism=(
        "CPython stdlib `ast`; module_functions + class_methods components"
    ),
    reference_mechanism=(
        "parso 0.8.7; `funcdef` nodes classified by their unwrapped syntactic "
        "parent"
    ),
    independent=True,
    known_mismatches=(
        "The reference reports constructors / overload_declarations / "
        "nested_functions separately so each definitional exclusion stays "
        "visible as evidence.",
    ),
)

#: JavaScript and TypeScript share one reference mechanism (the TypeScript
#: compiler API) but are mapped and reported separately, because the construct
#: populations differ: TS adds interfaces, type aliases, enums and
#: declaration-only signatures that JS cannot express.
_JS_TS_TYPE_TREATMENT = (
    "Named class declarations and class expressions held by a stable "
    "module-scope variable or exact CommonJS assignment count. EXCLUDED on "
    "both sides: interfaces, type aliases and enums, which ArchLens tracks as "
    "separate components that do not feed classes_structs. Class expressions "
    "without a stable module binding are excluded even when they carry an "
    "internal name."
)
_JS_TS_METHOD_TREATMENT = (
    "Named module-level function declarations, arrow/function expressions "
    "held by stable named module-scope variables or exact CommonJS export "
    "assignments, and direct methods of counted classes or object literals "
    "bound directly to a module-scope identifier count, only with a body. "
    "`get`/`set` accessors ARE counted under the same direct-owner rule. "
    "EXCLUDED on both sides: constructors, overload signatures and other "
    "declaration-only members, anonymous default function declarations, "
    "functions nested inside another function, anonymous callbacks, methods "
    "of nested/property-assigned/inline object literals, and methods of "
    "uncounted class expressions. Methods of named local class declarations "
    "remain direct class methods and count."
)

JAVASCRIPT_TYPES = DefinitionMapping(
    identifier="classes_structs.javascript_class.v1",
    metric="classes_structs", languages=("JavaScript",),
    unit_counted="named class declarations and class expressions",
    comment_treatment="not applicable", blank_treatment="not applicable",
    construct_treatment=_JS_TS_TYPE_TREATMENT,
    selection_treatment="Track A: file set fixed by ArchLens",
    archlens_mechanism="tree-sitter-javascript 0.25.0",
    reference_mechanism="TypeScript 5.6.3 compiler API, parse only (ScriptKind JS/JSX)",
    independent=True,
)

JAVASCRIPT_METHODS = DefinitionMapping(
    identifier="methods_functions.javascript_declared.v1",
    metric="methods_functions", languages=("JavaScript",),
    unit_counted=(
        "named/stably-bound module functions and direct methods of reviewed "
        "named class/object owners, with a body"
    ),
    comment_treatment="not applicable", blank_treatment="not applicable",
    construct_treatment=_JS_TS_METHOD_TREATMENT,
    selection_treatment="Track A: file set fixed by ArchLens",
    archlens_mechanism="tree-sitter-javascript 0.25.0",
    reference_mechanism="TypeScript 5.6.3 compiler API, parse only",
    independent=True,
)

TYPESCRIPT_TYPES = DefinitionMapping(
    identifier="classes_structs.typescript_class.v1",
    metric="classes_structs", languages=("TypeScript",),
    unit_counted="named class declarations and class expressions",
    comment_treatment="not applicable", blank_treatment="not applicable",
    construct_treatment=_JS_TS_TYPE_TREATMENT,
    selection_treatment="Track A: file set fixed by ArchLens",
    archlens_mechanism="tree-sitter-typescript 0.23.2",
    reference_mechanism=(
        "TypeScript 5.6.3 compiler API, parse only (ScriptKind TS/TSX)"
    ),
    independent=True,
    known_mismatches=(
        "Declaration-only `.d.ts` files are excluded from measurement by the "
        "exclusion policy, so they never reach Track A.",
    ),
)

TYPESCRIPT_METHODS = DefinitionMapping(
    identifier="methods_functions.typescript_declared.v1",
    metric="methods_functions", languages=("TypeScript",),
    unit_counted=(
        "named/stably-bound module functions and direct methods of reviewed "
        "named class/object owners, with a body"
    ),
    comment_treatment="not applicable", blank_treatment="not applicable",
    construct_treatment=_JS_TS_METHOD_TREATMENT,
    selection_treatment="Track A: file set fixed by ArchLens",
    archlens_mechanism="tree-sitter-typescript 0.23.2",
    reference_mechanism="TypeScript 5.6.3 compiler API, parse only",
    independent=True,
    known_mismatches=(
        "Overload signatures are common in TypeScript and are excluded on both "
        "sides; the reference reports `bodyless_declarations` as evidence.",
    ),
)

#: Go entities. `classes_structs` maps to named struct types; Go has no class.
GO_TYPES = DefinitionMapping(
    identifier="classes_structs.go_struct.v1",
    metric="classes_structs", languages=("Go",),
    unit_counted="named struct type declarations",
    comment_treatment="not applicable", blank_treatment="not applicable",
    construct_treatment=(
        "EXCLUDED on both sides: interface types and other named types "
        "(aliases, named basic types), which are not structs. Anonymous struct "
        "types in field or variable positions are excluded."
    ),
    selection_treatment="Track A: file set fixed by ArchLens",
    archlens_mechanism="tree-sitter-go 0.25.0; `structs` component",
    reference_mechanism="Go 1.23.12 go/parser + go/ast, TypeSpec/StructType",
    independent=True,
)

GO_METHODS = DefinitionMapping(
    identifier="methods_functions.go_func_and_receiver.v1",
    metric="methods_functions", languages=("Go",),
    unit_counted="package-level functions plus methods with a receiver",
    comment_treatment="not applicable", blank_treatment="not applicable",
    construct_treatment=(
        "Receiver methods count on both sides (ArchLens: `receiver_methods`). "
        "EXCLUDED: function literals / closures, and declarations with no body "
        "such as assembly stubs."
    ),
    selection_treatment="Track A: file set fixed by ArchLens",
    archlens_mechanism=(
        "tree-sitter-go 0.25.0; module_functions + receiver_methods components"
    ),
    reference_mechanism="Go 1.23.12 go/parser + go/ast, FuncDecl",
    independent=True,
)

REGISTRY: dict[str, DefinitionMapping] = {
    mapping.identifier: mapping
    for mapping in (
        LOC_LINE_CLASSIFICATION,
        SOURCE_FILE_COUNT,
        JAVA_TYPES,
        JAVA_METHODS,
        PYTHON_TYPES,
        PYTHON_METHODS,
        JAVASCRIPT_TYPES,
        JAVASCRIPT_METHODS,
        TYPESCRIPT_TYPES,
        TYPESCRIPT_METHODS,
        GO_TYPES,
        GO_METHODS,
        SOURCE_SELECTION,
    )
}

#: Entity mappings by (metric, language), for the Track A driver.
ENTITY_MAPPINGS: dict[tuple[str, str], DefinitionMapping] = {
    (mapping.metric, language): mapping
    for mapping in (
        JAVA_TYPES, JAVA_METHODS, PYTHON_TYPES, PYTHON_METHODS,
        JAVASCRIPT_TYPES, JAVASCRIPT_METHODS, TYPESCRIPT_TYPES,
        TYPESCRIPT_METHODS, GO_TYPES, GO_METHODS,
    )
    for language in mapping.languages
}


#: Metric/language pairs whose *definitions* cannot be mapped onto each other,
#: so a numeric comparison would be meaningless however well the reference runs.
#:
#: Distinct from a missing toolchain. A missing toolchain is a capability gap
#: closeable by provisioning; a definition gap is not, and conflating the two
#: would hide which is which. Empty today: every supported metric/language pair
#: has a reviewed mapping, and every reference is provisioned.
NOT_COMPARABLE_DEFINITIONS: dict[tuple[str, str], str] = {}

#: Metric/language pairs with no independent reference **executable** here.
#: Empty since the reference environment was provisioned; retained because
#: availability is checked at run time and a broken toolchain must still be
#: recorded explicitly rather than producing an absent row.
UNAVAILABLE_REFERENCES: dict[tuple[str, str], str] = {}


def mapping_for(identifier: str) -> DefinitionMapping:
    try:
        return REGISTRY[identifier]
    except KeyError:
        raise KeyError(
            f"unknown definition mapping {identifier!r}; known: "
            f"{', '.join(sorted(REGISTRY))}"
        ) from None


def coverage_report() -> dict[str, Any]:
    """What this study can and cannot validate, stated up front."""
    covered: list[dict[str, Any]] = []
    for mapping in REGISTRY.values():
        for language in mapping.languages:
            covered.append({
                "metric": mapping.metric,
                "language": language,
                "definition_mapping_id": mapping.identifier,
            })
    # Both gap kinds are reported, and kept apart. A missing toolchain is a
    # capability gap that provisioning can close; a definition gap cannot be
    # closed by provisioning at all, and conflating them would hide which is
    # which.
    not_covered = [
        {
            "metric": metric, "language": language, "reason": reason,
            "gap_kind": "reference_unavailable",
        }
        for (metric, language), reason in UNAVAILABLE_REFERENCES.items()
    ] + [
        {
            "metric": metric, "language": language, "reason": reason,
            "gap_kind": "not_comparable_definition",
        }
        for (metric, language), reason in NOT_COMPARABLE_DEFINITIONS.items()
    ]
    order = lambda item: (item["metric"], item["language"])  # noqa: E731
    return {
        "covered": sorted(covered, key=order),
        "not_covered": sorted(not_covered, key=order),
        "fully_covered": not not_covered,
    }
