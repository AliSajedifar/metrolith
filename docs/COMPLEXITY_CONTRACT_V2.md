# Complexity Contract 2.0.0

**Normative.** Supersedes Complexity Contract 1.0.0 by **addition only**: every
1.0.0 rule, metric, status and invariant is carried forward **unchanged**, and
2.0.0 adds one metric — **ArchLens Cognitive Complexity**.

Pairs with **Artifact Schema 1.10.0**. **Metric Contract stays 3.0.0**: the four
benchmark metrics do not move, and Complexity Contract is registered as a
separate, non-blocking comparability dimension so a complexity-only difference
degrades a complexity delta rather than refusing an entire diff.

## Provenance

Sections 1–11 are `docs/COMPLEXITY_CONTRACT_V1.md`, unchanged. Section 12 is the
cognitive metric, and it is **frozen rule table revision 3** — the table at
`validation/complexity_g1a_20260811/FROZEN_RULE_TABLE.md`, sha256
`6af37612aded59ac3efa540b2b32f4afa8b9991ca0403c927d65f5410ca3479b`, accepted by
the owner through Amendments 001 and 002. **No semantic rule was changed in the
course of writing this contract.** Where this document and that table could be
read differently, the table governs and the difference is a defect here.

---

## 12. ArchLens Cognitive Complexity

### 12.1 Naming

**ArchLens Cognitive Complexity.** Inspired by the published Cognitive
Complexity model; **not** Sonar-compatible and never described as such. G0
established by measurement that the Sonar-lineage implementations do not agree
with each other — PMD, gocognit, eslint-plugin-sonarjs and the
`cognitive_complexity` package disagree on `||` sequences, on recursion, and on
parenthesis handling. There is no single behaviour to be compatible with.

### 12.2 Population

Exactly the canonical `methods_functions` population of §2 — **unchanged**. No
callable is added, removed or reclassified by this contract. Every canonical
callable that receives the 1.0.0 metrics receives this one.

### 12.3 The four kinds of increment

A callable starts at **0** — not 1. That is the first difference from cyclomatic
complexity and it has a persistence consequence (§12.7).

| Kind | Prefix | Contribution | Raises nesting |
|---|---|---|---|
| structural | `S-` | `1 + nesting_level` | yes |
| flat | `F-` | `1` | no |
| nesting-only | `N-` | `0` | yes — **reserved, currently no members** |
| zero | `Z-` | `0` | no |
| boundary | `B-` | subtree excluded | — |

### 12.4 Rules

27 normative rules. Applicability per language is the matrix in the frozen
table §9 — 84 applicable cells.

**Structural (7):** `S-IF` · `S-LOOP` · `S-SWITCH` (whole construct, arms 0) ·
`S-SELECT` (Go) · `S-CATCH` · `S-TERNARY` · `S-COMPREHENSION` (Python, one per
comprehension however many generator clauses).

**Flat (9):** `F-ELSEIF` · `F-ELSE` · `F-LOOPELSE` · `F-TRYELSE` ·
`F-BOOLSEQ` · `F-GUARD` · `F-COMPIF` · `F-LABELJUMP` · `F-RECURSION`.

**Zero (10):** `Z-TRY` · `Z-FINALLY` · `Z-JUMP` · `Z-OPTCHAIN` ·
`Z-SYNCHRONIZED` · `Z-WITH` · `Z-DEFER` · `Z-ASSERT` · `Z-TYPEONLY` ·
`Z-BITWISE`.

**Boundary (1):** `B-NESTED`.

Three rules carry mechanics a reader must not infer:

* **`F-BOOLSEQ`** — one increment per **maximal run of the same short-circuit
  operator in flattened source order**. Parentheses are transparent; a different
  operator between two runs of the same operator splits them, so
  `a || b && c || d` is **3**. Operator sets: `&&`/`||` (Java, Go),
  `and`/`or` (Python), `&&`/`||`/`??` (JS/TS). Logical assignments `&&=`, `||=`,
  `??=` are **not** operators of this rule and contribute 0, while their
  operands remain traversable.
* **`F-RECURSION`** — **at most +1 per callable**, however many call sites.
  Recognized: a bare self-name call, or a member call whose receiver is the
  language's explicit current receiver (`this`, `self`, `cls`, the declared Go
  receiver) or the enclosing type name. `arbitraryObject.sameName()` is not
  recursion. Aliases are false negatives, mutual recursion is undetected,
  dynamic dispatch is unresolved — ArchLens performs no call resolution and
  these limits ship with the metric.
* **`B-NESTED`** — **discovery and attribution are different traversals.**
  Discovery is global and reaches through enclosing scopes to find every
  canonical callable. Attribution stops at every nested callable boundary: a
  nested callable contributes nothing to its parent, and receives its own row if
  it is in the population, or is measured nowhere if it is not.

Precedence: a specialized rule suppresses the generic rule it overlaps
(`F-ELSEIF` not `F-ELSE`+`S-IF`; `F-LOOPELSE`/`F-TRYELSE` not `F-ELSE`;
`F-COMPIF` and `F-GUARD` not `S-IF`) but **never** suppresses the operands, so a
guard or comprehension condition still contributes its own boolean sequences.

Default traversal: a construct with no rule contributes 0 **and its children are
still traversed**, unless an explicit subtree exclusion (`B-NESTED`,
`Z-TYPEONLY`) applies.

### 12.5 Deliberate divergence from Complexity Contract 1.0.0

Both metrics ship in this contract and they disagree on the same source, by
design. Stated so a reader does not treat one as a check on the other:

| Construct | Cyclomatic (§7, unchanged) | Cognitive (§12) |
|---|---|---|
| `switch` | +1 per arm | +1 for the whole construct |
| boolean operators | +1 per operator token | +1 per maximal same-operator run |
| recursion | +0 | +1 per callable |
| `else` | +0 | +1 |
| `finally`, Python `with`, Java `synchronized` | nesting-increasing | no increment, **no nesting** |
| Python comprehension | +1 per `for` and per `if` clause | one per comprehension, +1 per `if` clause |

### 12.6 Field

| Field | Kind |
|---|---|
| `cognitive_complexity` | count ≥ **0**, or null |

No composite score, no threshold, no rating. This contract publishes a number
and its provenance, not a verdict.

### 12.7 Status, and the never-zero rule

**`cognitive_complexity` is governed by `structural_complexity_status`.** It is
computed in the same traversal, over the same selected tree, behind the same
boundary rule as the six tree-derived 1.0.0 metrics, so its availability is not
independent of theirs. No second per-callable status is introduced; a field that
must always equal another is one fact and a drift surface, not two facts.

> **`structural_complexity_status == failed` ⇒ `cognitive_complexity` IS NULL.**
> A failed, unavailable or not-evaluable measurement is **never** written as
> `0`.

This is sharper than the equivalent 1.0.0 rule. `cyclomatic_complexity` has
`minimum: 1`, so a zero is impossible and self-evidently wrong.
`cognitive_complexity` has **`minimum: 0`** and 0 is an ordinary measured value —
most real callables score it. Nullability carries the entire distinction, and
three states must stay apart:

| Situation | Persisted |
|---|---|
| measured, no construct | `0` |
| measured, unavailable | `null` |
| run predates Artifact 1.10 | **column absent** |

Absent is not null, and null is not zero.

### 12.8 Measurement state

`cognitive_measurement_state`, per repository and per run, over a closed
vocabulary: `measured` · `partial` · `failed` · `not_applicable`. Its **absence**
means the run predates Artifact Schema 1.10.0 and is not the same fact as
`failed`.

### 12.9 Completeness

A native 1.10 run whose `cognitive_measurement_state` is `measured` or `partial`
**must** carry the callable artifact family, and its persisted rows must
reconcile. The check reads the staged and written **bytes** through the strict
reader — never producer memory — and runs as its own finalization layer, after
structural validation and before the manifest, with `run_status.json` committed
last.

### 12.10 Invariants

Every 1.0.0 invariant is carried forward. Added:

1. `cognitive_complexity >= 0` for every emitted row where it is non-null.
2. `structural_complexity_status == failed` ⇒ `cognitive_complexity IS NULL`.
3. `structural_complexity_status == complete` ⇒ `cognitive_complexity` is
   non-null; a measured callable always has a value, and `0` is a value.
4. `cognitive_measurement_state ∈ {measured, partial}` ⇒ the callable artifact
   family exists and reconciles.
5. A genuinely empty scope is `not_applicable` with no rows — never `measured`
   with zero rows.

### 12.11 What 2.0.0 does not add

The original 2.0.0 metric definition did not itself add aggregates, report
sections, diff dimensions, Policy rules or thresholds. Metrolith 4.0.0 also
provides integrations described in [USAGE](USAGE.md); their separate contracts
do not change the metric and state definitions in this document.
