# ArchLens Complexity Contract 1.0.0

**Status:** normative for `COMPLEXITY_CONTRACT_VERSION = "1.0.0"`.
**Applies to:** per-callable complexity records and complexity aggregates written
by Artifact Schema 1.9.0 and later, whose `complexity_contract_version` is
`1.0.0`.
**Does not change:** Metric Contract 3.0.0, Inventory Schema 1.7.0, Exclusion
Policy 1.5.0, or `analysis_scope_hash` construction 2.0.0.
**Ratified:** 2026-08-10.

Grammar facts cited below were established by the historical C0.1 probe. Its
raw output at `validation/complexity_c0_20260810/` is archived and is not shipped
in this source distribution. These structural definitions are carried forward
by [Complexity Contract 2.0.0](COMPLEXITY_CONTRACT_V2.md).

---

## 1. Why this is a separate contract

Metric Contract 3.0.0 is normative for the four benchmark metrics, and none of
them changes. Complexity is a new metric family with its own definitions, its own
failure modes and its own evolution path, so it takes its own version.

The separation is also load-bearing for comparison. `metric_contract_version` is
a **blocking** comparability dimension in `archlens diff`: when it differs, the
comparison is refused and no metric delta is produced at all. Folding complexity
into Metric Contract 3.1.0 would therefore make every diff between a new run and
every run ever produced refuse outright. `complexity_contract_version` is
registered as a **non-blocking** dimension instead: when it differs, complexity
deltas become `not_evaluable` and the four benchmark metrics still diff.

---

## 2. The canonical callable population

**Complexity Contract 1.0.0 measures exactly the callables that contribute to
`methods_functions`. It does not measure every executable callable construct in
every language.**

`methods_functions = module_functions + class_methods + receiver_methods`.
The population is therefore:

| Language | In the population |
|---|---|
| **Python** | module-level `def` / `async def`; direct class-body methods, including methods of a class declared inside a function |
| **Java** | `method_declaration` with a body whose owner is a named `class_body`, `interface_body` or `enum_body` — including interface `default`/`static` methods, enum-body methods, and methods of named local classes |
| **JavaScript / TypeScript** | module-scope `function_declaration` and `generator_function_declaration`; function/arrow/generator expressions bound to a stable named module-scope variable or an exact CommonJS export target; `method_definition` in the body of a counted class or of an object literal bound directly to a module-scope identifier — including `get`/`set` accessors, `static` and `#private` methods |
| **Go** | module-scope `function_declaration` and `method_declaration`, each with a body |

**Outside the population**, because they are outside `methods_functions`:
constructors (Java constructors and compact constructors, Python `__init__` and
`__new__`, JS/TS `constructor`), lambdas, nested functions, anonymous functions,
anonymous-class methods, signature-only declarations (`@overload`, TypeScript
overload signatures, abstract and interface method declarations, Go body-less
declarations), and Java static/instance initializer blocks.

A callable whose name, parameters or body is missing, or which lies inside a
malformed subtree, is **not emitted**. This applies the same rejection the entity
counters apply, which is what keeps §5 true.

### 2.1 Recorded population properties

* Functions declared inside a TypeScript `namespace` or a bare block classify as
  module scope and **are** counted. Confirmed by probe; unchanged from current
  behaviour.
* An anonymous `export default function () {}` is counted nowhere today and is
  therefore outside this population.
* Python's entity visitor descends only statement bodies, so a `lambda` in a
  decorator, default argument or annotation is never visited. This cannot affect
  the population — a `def` cannot occur in an expression position.

---

## 3. Traversal rule

> A measured callable's structural metrics describe **its own executable control
> flow**. Traversal stops at every nested callable boundary. If the nested
> callable is itself in the canonical population it receives its own record and
> its own traversal, beginning at nesting depth 0 and cyclomatic base 1. If it is
> not in the population, its body is **excluded** — its decisions, boolean
> operators and nesting contribute to no record.

### 3.1 Boundary node sets

| Language | Boundary nodes |
|---|---|
| **Python** | `FunctionDef`, `AsyncFunctionDef`, `Lambda`, `ClassDef` |
| **Java** | `method_declaration`, `constructor_declaration`, `compact_constructor_declaration`, `lambda_expression`, `class_declaration`, `record_declaration`, `enum_declaration`, `interface_declaration`, `object_creation_expression` carrying a `class_body`, `static_initializer`, and a bare `block` whose parent is `class_body` (instance initializer) |
| **JavaScript / TypeScript** | `function_declaration`, `generator_function_declaration`, `function_expression`, `arrow_function`, `generator_function`, `method_definition`, `class_declaration`, `abstract_class_declaration`, class expressions |
| **Go** | `function_declaration`, `method_declaration`, `func_literal` |

Boundary detection **must iterate named nodes only**. Iterating all nodes reports
a node of type `class` for the `class` keyword token, which is not a class
expression; treating it as a boundary would truncate traversal at every class
keyword.

---

## 4. Coverage limitation (normative)

> **`Σ cyclomatic_complexity` over records is not the total decision count of a
> file.** Control flow inside constructors, lambdas, callbacks, nested
> functions, anonymous-class methods and initializer blocks is measured nowhere
> under this contract. A per-callable complexity value is a property of that
> callable, not a decomposition of the file.

No field quantifies the excluded volume in 1.0.0. If adjudication shows a
disagreement class cannot be explained without one, it is added at 1.1.0 with
that evidence — not speculatively.

Consumers, reports and external claims must not describe these values as
whole-file or whole-repository complexity totals.

---

## 5. Keystone invariant, status-gated

The record count and `methods_functions` are two computations over the same
selected tree using the same rejection rules. The equality is asserted only where
the measurement is evaluable.

| `methods_functions_status` | Rule | Reconciliation outcome |
|---|---|---|
| `complete` | `count(records) == methods_functions` **exactly** | `exact`, residual 0 |
| `partial` | equality **still holds** — both sides reject the same malformed declarations — but the shared value is a **partial observation** | `exact`, annotated `observation_quality: "partial"` |
| `failed` | `methods_functions` is null. **No comparison is performed.** | `not_evaluable` |
| `not_applicable` | nothing was measurable | `not_evaluable` |

Two rules follow, and both are absolute:

1. **A partial equality is not a verified count.** It shows the two computations
   agree about what they could observe. It says nothing about the malformed
   region. This mirrors the standing rule that a partial numeric zero is not a
   verified complete zero.
2. **Zero records under a failed measurement is never a measured zero.** A
   genuinely empty file and an unmeasurable file must stay distinguishable, which
   is why §12.2 persists a per-file status and `callable_count` where `0` and
   `null` mean different things.

The gate applies identically at file, language and repository level, and to the
`callable_count` aggregate.

---

## 6. Record identity

Each record carries `callable_row_id`:

```
callable_row_id = "sha256:" + sha256(
    relative_path ␟ language ␟ callable_kind ␟ qualified_name
                  ␟ signature_discriminator ␟ ordinal
)
```

joined with `\x1f`, absent components rendered as the empty string. All six
components are persisted individually, so the value is auditable rather than
opaque.

| Component | Rule |
|---|---|
| `relative_path` | repository-relative POSIX path; never absolute |
| `language` | detected language |
| `callable_kind` | `module_function`, `class_method`, `receiver_method` |
| `qualified_name` | lexical owner chain joined by `.`; non-callable blocks contribute no segment |
| `signature_discriminator` | Java and TypeScript only: the parenthesized parameter type list **as written in source**, whitespace-collapsed. `null` elsewhere |
| `ordinal` | document-order index among colliding siblings; `null` when the first five components are already unique within the file |

`row_id_basis` records which of `qualified`, `qualified_with_signature` or
`positional` was required.

For Go receiver methods `qualified_name` is `<ReceiverTypeName>.<MethodName>`.
Pointer-ness is **excluded** from identity and recorded separately as
`receiver_is_pointer`: Go forbids declaring both a value- and a pointer-receiver
method of the same name on one type, so it is not needed for uniqueness, and
excluding it keeps the identity stable across a receiver-form change.

### 6.1 Explicit non-claims

* `callable_row_id` is **row identity within one artifact**. It is **not** a
  stable cross-revision identity and must not be used as one.
* The overload discriminator is **syntactic**. ArchLens has no symbol table, so
  two overloads written with a type alias and with its fully qualified form
  produce different discriminators.
* `start_line` and `end_line` are evidence, never identity. Metric values are
  never identity.
* No rename or fuzzy matching exists under this contract.

---

## 7. Cyclomatic Complexity

**ArchLens Syntactic Cyclomatic Complexity.** Decision-point counting over the
concrete syntax tree. **Not** a control-flow-graph-derived McCabe number; no CFG
is built. Documentation, artifacts and reports say "syntactic". The word
"McCabe" is not used for this value.

Every measured callable starts at **1**:

```
cyclomatic_complexity == 1 + decision_point_count + boolean_operator_count
```

This identity is enforced mechanically, so the decomposition can never drift from
the total.

### 7.1 `decision_point_count`

| Construct | Increment |
|---|---|
| `if` — each `else if` is a nested `if` in these grammars | +1 each |
| bare `else` | **+0** |
| `for`, `while`, `do..while`, `for..in`, `for..of`, Go `for`, Java enhanced-for | +1 each |
| `switch` / `match` case arm | +1 per arm; `default` and a bare wildcard `case _:` **+0** |
| Java `case X when g` | +1 arm, +1 guard |
| Python `case P if g` | +1 guard |
| `catch` / `except` clause | +1 each; `try`, `finally` and Python `try…else` **+0** |
| ternary / conditional expression, including Python `a if c else b` | +1 |
| Python comprehension | +1 per `for` clause, +1 per `if` clause |
| Go `select` case and type-switch case | +1 per case; `default` **+0** |
| JS optional chaining `?.` | **+0** |
| labelled `break` / `continue` | **+0** |
| recursion | **+0** — requires call resolution ArchLens does not have |

### 7.2 `boolean_operator_count`

Short-circuit operator **token occurrences**, anywhere in the callable's own
traversed content:

| Language | Counted | Node types |
|---|---|---|
| Java | `&&`, `\|\|` | `binary_expression` |
| JavaScript / TypeScript | `&&`, `\|\|`, `??`, `&&=`, `\|\|=`, `??=` | `binary_expression`, `augmented_assignment_expression` |
| Python | `and`, `or` — an `n`-value `BoolOp` contributes `n − 1` | `BoolOp` |
| Go | `&&`, `\|\|` | `binary_expression` |

Not counted: bitwise `&` and `|`; Python chained comparisons (`a < b < c` is one
comparison chain and contributes zero); unary `not` / `!`.

---

## 8. Function NLOC

`nloc` is the number of physical lines in the callable's declaration span that
are neither blank nor comment-only, **after the same comment masking the
repository-level `lines_of_code` metric applies**. The repository line
classifier is reused rather than reimplemented, which makes function NLOC
consistent with repository LOC by construction.

| Case | Treatment |
|---|---|
| blank, comment-only | excluded |
| code with a trailing comment | counts once |
| Python docstring | **code** — a string expression, not a comment |
| multiline strings, template literals, Go raw strings | code |
| signature line, closing brace line | code |
| lexically nested callables | **inside the span** — see §8.2 |

### 8.1 Span

Four line fields are recorded: `start_line`, `end_line`, `body_start_line`,
`body_end_line`. `nloc` is measured over `[start_line, end_line]`, and the span
**includes attached decorators and annotations**. Where the grammar places them
differs, and the rule is per language:

| Language | Decorator / annotation placement | Span rule |
|---|---|---|
| **Java** | annotations are inside `modifiers`, inside the declaration node | span already includes them |
| **Python** | `ast.FunctionDef.lineno` points at `def`; decorators are a separate `decorator_list` | extend `start_line` to `min(d.lineno for d in decorator_list)` |
| **JavaScript / TypeScript** | decorators are **preceding siblings**, not children — a decorated method's `start_line` is the line *after* its decorator | extend `start_line` backwards over contiguous preceding sibling `decorator` nodes |

The JS/TS row was established by probe, not assumed. Without it a decorated
TypeScript method reports a span shorter than the source a reader sees as that
method.

### 8.2 Deliberate asymmetry

NLOC is a **span size** measure and includes lexically nested callables, while
the structural metrics of §3 exclude them. A callback written inside a function
is part of that function's size but not of its control flow. This is a decision,
not an oversight, and it is why NLOC carries its own status (§12.1).

Because spans of a measured callable containing another measured callable
overlap, **per-callable NLOC is not additive** and no NLOC total is published.

### 8.3 Bytes measured are bytes analyzed

No line-ending normalization is applied, ever. Every JS/TS compatibility recovery
strategy is a same-width, in-place byte substitution, so physical line count and
line numbering survive recovery intact — verified by probe across all four
strategies, individually and end-to-end. Recovery alone therefore does not
degrade `nloc_status`.

`location_maps_to_original_source` remains required for a different reason: the
UTF-16 → UTF-8 conversion path can leave **byte** offsets unmappable even though
line numbers hold. The field keeps that distinction visible.

---

## 9. `formal_parameter_count`

The number of **formal parameters declared in the callable's own parameter list,
as written in source**. This is a syntactic declaration count. It makes no claim
about invocation, binding or arity.

**These values are not measurement-equivalent across languages.** The languages
declare parameters differently, and a cross-language comparison of this field
carries the same caveat Metric Contract 3.0.0 already states for the entity
counts.

| Language | Rule | Receiver / `self` / `this` |
|---|---|---|
| **Python** | `len(posonlyargs) + len(args) + len(kwonlyargs)`, `+1` if `vararg`, `+1` if `kwarg`. Defaults change nothing | `self` / `cls` **are counted** — they are declared formal parameters and the rule is purely syntactic |
| **Java** | count `formal_parameters` children of type `formal_parameter` or `spread_parameter`. Varargs `T... x` is **one** | `receiver_parameter` is a distinct node type and is **excluded**. Counting `named_children` would wrongly include it |
| **JavaScript** | one per top-level parameter node. A destructuring pattern is **one**. Rest `...args` is **one**. A default is one | n/a |
| **TypeScript** | as JavaScript | the `this` parameter is **excluded**; `declares_typescript_this_parameter` records that the exclusion applied |
| **Go** | per `parameter_declaration`, count its `identifier` children; if it has none, count 1. A `variadic_parameter_declaration` counts as **one** | the receiver is a separate field and is **excluded**; `receiver_type_name` and `receiver_is_pointer` record it |

Worked Go cases, confirmed by probe: `func grouped(a, b int, c string)` → **3**;
`func unnamed(int, string)` → **2**; `func variadic(prefix string, rest ...int)`
→ **2**.

No flag is recorded for Python's `self`. A flag is warranted only where a
declared parameter is **subtracted** from the published number — the TypeScript
`this` case — because there the flag explains the number. Python's `self` is
included, so the number already speaks for itself.

---

## 10. `max_nesting_depth`

The maximum, over statements in the callable's **own** traversed content, of the
number of enclosing nesting-increasing constructs. The callable body is depth
**0**.

**Nesting-increasing**, on entering the body: `if` / `else if` / `else`
branches; every loop body; a `switch` / `match` body **and** each case body, so a
statement inside a case inside a switch is depth 2; `try`, each `catch` /
`except`, and `finally`; Python `with`; **Java `synchronized`**; Go `select` and
each `select` case.

**Not nesting-increasing:** the callable's own declaration; a bare block not
attached to a control construct; parenthesized or grouped expressions; a ternary;
a comprehension's `for` clause, which is an expression rather than a block.

**Nesting is structural nesting.** A construct may open a level while
contributing nothing to cyclomatic complexity, and several do: Python `with`,
`try` and `finally`, case bodies, and Java `synchronized` each add a level and
**0** decisions. The two metrics answer different questions, so their construct
sets are deliberately not the same set.

Traversal stops at every boundary (§3), so nothing inside any nested callable —
measured or not — affects the parent's depth.

This is deliberately **not** the same as cognitive-complexity nesting: it counts
`else`, `finally` and `with`, which the cognitive model treats differently. The
two must not be equated if Complexity Contract 2.0.0 arrives.

---

## 11. Condition shape

`max_condition_operator_count` is the largest number of short-circuit operators
appearing within any single **decision expression** — the condition of an `if`,
loop or ternary, or a `case` guard. It is `0` when there are none.

| Decision expression | Value |
|---|---|
| `a` | 0 |
| `a && b` | 1 |
| `a && b && c` | 2 |
| `(a && b) \|\| c` | 2 |
| `a < b < c` (Python chained comparison) | 0 |
| `a ?? b` | 1 |
| `x != null && (y \|\| z)` | 2 |
| `case P if g1 and g2` | 2 |

**No threshold appears in this contract.** Every comparable ecosystem definition
of a "complex condition" is a threshold rule with a conventional default of three
operators per expression. A default threshold would be a numeric quality gate,
which this contract does not contain and which the Policy v1 guard exists to
prevent. Any threshold view remains a consumer's choice, derivable from this
field.

`branch_count` and `complex_condition_count` are deliberately absent: neither
carries information that `decision_point_count`, `boolean_operator_count` and
`max_condition_operator_count` do not.

---

## 12. Record shape and statuses

### 12.1 Two statuses

| Status | Governs | Failure mode it represents |
|---|---|---|
| `structural_complexity_status` | `cyclomatic_complexity`, `decision_point_count`, `boolean_operator_count`, `max_condition_operator_count`, `max_nesting_depth`, `formal_parameter_count` | traversal of the selected callable tree |
| `nloc_status` | `nloc` and the line/location fields | span and location mapping, which can fail independently of traversal |

Both use `complete` / `partial` / `failed` / `not_applicable`. No per-metric
status exists; the two failure modes are distinct and the six structural metrics
share one.

### 12.2 File-level status and `callable_count`

Per-file `structural_complexity_status`, `nloc_status` and `callable_count` are
persisted as columns of the per-file contribution ledger, whose row contract
becomes `contribution_row-1.9`. They are **not** added to the file inventory, so
Inventory Schema remains 1.7.0.

The three cases stay distinguishable:

| Situation | `structural_complexity_status` | `callable_count` |
|---|---|---|
| measured, genuinely contains no callable | `complete` | **`0`** — a verified zero |
| parse failed | `failed` | **`null`** |
| partial parse, some callables observable | `partial` | integer — a partial observation |
| not a measurable source | `not_applicable` | `null` |

`complete` with `0` and `failed` with `null` must never be conflated.

### 12.3 Failure semantics

* Metrics are nullable. A null is unavailable and is **never** read as zero.
* A callable inside a malformed subtree is not emitted, so §5 still holds.
* A file whose parse failed emits no records and carries `failed` statuses.

---

## 13. Aggregates

Published at repository and language level only. No persisted file-level
aggregate: it is derivable from the records and would add a third reconciliation
surface for no new information.

`callable_count` · `cyclomatic_complexity_total` · `cyclomatic_complexity_mean`
(size-sensitive; meaningful only beside `callable_count`) ·
`cyclomatic_complexity_median` · `cyclomatic_complexity_max` · `nloc_median` ·
`nloc_max` · `max_nesting_depth_median` · `max_nesting_depth_max` ·
`formal_parameter_count_median` · `formal_parameter_count_max`.

Median is the **lower median** for even `N` — the `N/2`-th value of the ascending
sort, 1-indexed — so the result is always an observed value rather than an
interpolated half-unit no callable has.

**Not published:** `nloc_total`, because spans overlap and a total would double
count; percentiles, which are derivable and are deferred until a concrete use
case appears; and any threshold count such as "functions above 10", which would
be a policy gate wearing a descriptive label.

Reconciliation follows the §5 status gate:
`Σ cyclomatic_complexity == cyclomatic_complexity_total`, and
`count(records) == callable_count == methods_functions` where evaluable. A null
aggregate under a `failed` status reconciles as `not_evaluable`, never as zero
and never as a residual.

---

## 14. Persistence and completeness

Records are written as a `callables` artifact family — `callables.csv`, or
`callables/NNNNN.csv` partitions with `callables/container.json` above the
partition threshold — mirroring the per-file contribution ledger.

**Completeness invariant.** For a run declaring Artifact Schema 1.9.0 or later:
if any repository has `complexity_status` of `complete` or `partial`, the
callable artifact family **must** be present and its row count must reconcile
with `callable_count`. Absence is permitted **only** when every repository is
`failed` or `not_applicable` **and** each carries a non-null
`complexity_unavailable_reason`.

The invariant is checked against **exact bytes**: the staged candidate terminal
documents and the written authoritative artifacts, read back from disk through
the strict reader with every artifact forced, never from producer memory. It runs
inside the candidate window, before commit, so the candidate → validate exact
bytes → commit protocol is preserved.

---

## 15. What is not in 1.0.0

**Cognitive Complexity is not part of this contract.** If it is adopted it
arrives as **Complexity Contract 2.0.0** under **Artifact Schema 1.10.0**, since
the callable row contract is `additionalProperties: false` with a fixed column
order and a new persisted field is a new artifact version. No column is reserved
for it here.

Adoption is gated by G0, which has three outcomes:

| Outcome | Condition |
|---|---|
| `PASS_FULL` | all five languages have a reviewed rule table, a synthetic corpus pinning each rule, **and** an executable independent reference |
| `PASS_LIMITED` | the contract is defensible but one or more languages have only rule/synthetic validation — **requires explicit owner approval**, and every product, resume and research claim is weakened accordingly |
| `FAIL` | the five-language contract is not defensible — stop and defer, naming the failing languages and unresolved rules |

`rule_validated_only` is a distinct, weaker evidence class and **never** counts
as independent validation. It is recorded per language in the definition mapping,
in the artifact, and in every external claim.

Also absent, deliberately: any composite or hotspot score; any good/bad/critical
ranking; any numeric policy threshold; control-flow, call or dependency graphs;
cross-revision callable identity; callable-level revision diff.

---

## 16. Versioning

| Change | Requires |
|---|---|
| a new persisted per-callable field | new Artifact Schema minor **and** a Complexity Contract bump |
| a change to any counting rule in §7–§11 | Complexity Contract **major** bump — the numbers stop meaning what they meant |
| a change to the §3 traversal rule or the §2 population | Complexity Contract **major** bump |
| adding an aggregate derived from existing fields | Complexity Contract **minor** bump |
| documentation clarification changing no measured value | no bump; record it here |

Complexity Contract versions are `MAJOR.MINOR.PATCH`. A contract change is a new
version, never an in-place edit of published bytes.

Navigation: [usage](USAGE.md) · [project README](../README.md).
