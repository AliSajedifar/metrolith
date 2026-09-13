# ArchLens Cognitive Complexity — frozen rule table (G1-A)

**Frozen 2026-08-11.** This is the owner-accepted rule table from
`validation/complexity_g0_20260811/G0_RECORD.md` §3, turned into identified,
individually addressable rules so a corpus case can cite the exact rule it pins.

**This is NOT a contract document.** Complexity Contract 2.0.0 does not exist and
is not created here. Nothing in this file is persisted by any artifact, no
production code implements it, and Complexity Contract 1.0.0 is untouched.
Freezing means only this: the rules below are fixed for the duration of G1, so
the corpus and any later implementation are written against one stationary
target rather than against each other.

**Naming.** ArchLens Cognitive Complexity. Never "Sonar Cognitive Complexity"
and never "Sonar-compatible" — G0 established that the Sonar-lineage tools do
not agree with each other.

---

## 1. The three kinds of increment

Every rule below is exactly one of these. A callable starts at **0**.

| Kind | Contribution | Raises nesting for its contents |
|---|---|---|
| **structural** | `1 + nesting_level` | yes, unless the rule says otherwise |
| **flat** | `1` | no |
| **nesting-only** | `0` | yes |
| **zero** | `0` | no |

`nesting_level` is the count of enclosing nesting-raising constructs **inside
the measured callable**. The callable's own body is level 0.

The distinction between *flat* and *structural* is the whole model: a construct
that adds a branch you must hold in your head scales with how deeply it is
buried; a construct that merely adds a term to a condition does not.

---

## 2. Rules

### 2.1 Structural

| ID | Construct | Contribution | Nesting |
|---|---|---|---|
| `S-IF` | `if` | `1 + n` | +1 for the branch body |
| `S-LOOP` | `for`, `for..in`, `for..of`, `for..range`, `while`, `do..while` | `1 + n` | +1 for the body |
| `S-SWITCH` | `switch` statement, switch expression, Go type switch, Python `match` | `1 + n` for the **whole construct** | +1 for the body |
| `S-SELECT` | Go `select` | `1 + n` | +1 for the body |
| `S-CATCH` | `catch` / `except` clause | `1 + n` | +1 for its body |
| `S-TERNARY` | conditional expression, incl. Python `a if c else b` | `1 + n` | +1 for both branches |
| `S-COMPREHENSION` | Python comprehension / generator expression | `1 + n` for the comprehension | +1 for its body |

### 2.2 Flat

| ID | Construct | Contribution |
|---|---|---|
| `F-ELSEIF` | `else if` / `elif` | `1` |
| `F-ELSE` | bare `else` | `1` |
| `F-LOOPELSE` | Python `for`/`while` `else` | `1` |
| `F-TRYELSE` | Python `try…else` | `1` |
| `F-BOOLSEQ` | one **maximal sequence** of a single short-circuit operator, anywhere in the callable | `1` per sequence |
| `F-GUARD` | Java `case X when g`, Python `case P if g` | `1` |
| `F-COMPIF` | Python comprehension `if` clause | `1` |
| `F-LABELJUMP` | labelled `break` / `continue`, Go `goto` | `1` |
| `F-RECURSION` | direct self-recursion, by lexical name match within the callable | `1` |

`F-ELSEIF` and `F-ELSE` raise nesting **for their own body only**, not for their
siblings: an `if / else if / else` chain measures 3 and leaves the chain flat.

### 2.3 Nesting-only

| ID | Construct | Contribution | Nesting |
|---|---|---|---|
| `N-FINALLY` | `finally` block | `0` | +1 for its body |

### 2.4 Zero

| ID | Construct | Why |
|---|---|---|
| `Z-TRY` | the `try` block itself | it introduces no branch; its `catch` does |
| `Z-JUMP` | unlabelled `break` / `continue` / `return` | no target to hold in mind |
| `Z-OPTCHAIN` | JS/TS `?.` | one guarded access, not a branch |
| `Z-SYNCHRONIZED` | Java `synchronized` | not a branch, and **not nesting-raising** |
| `Z-WITH` | Python `with` | as `synchronized` |
| `Z-DEFER` | Go `defer` / `go` statement | the statement is not a branch; the literal it carries is a boundary |
| `Z-ASSERT` | Python `assert`, `raise`, `yield`, `await` | no branch |
| `Z-TYPEONLY` | TS type annotations, `as`, `!`, conditional **types**, overload signatures, decorators | types are not control flow |
| `Z-BITWISE` | `&`, `\|` and Java multi-catch `A \| B` | not short-circuit operators |

### 2.5 Boundary

| ID | Rule |
|---|---|
| `B-NESTED` | **Traversal stops at every nested callable boundary.** A lambda, arrow, closure, `func_literal`, nested `def`, callback, anonymous-class method or class-in-function contributes **nothing** to the enclosing callable — not its increments and not its nesting. Identical to Complexity Contract 1.0.0 §5.1, deliberately, so both metrics measure one population. |

---

## 3. Deliberate divergences from Complexity Contract 1.0.0

Recorded because a reader will otherwise assume the two agree on the same file.

| Construct | CX (Cyclomatic 1.0.0) | CG (Cognitive, frozen here) |
|---|---|---|
| `switch` | **+1 per arm** | **+1 for the whole construct**, arms 0 |
| Python `with` | nesting-raising (§10) | `Z-WITH`: no increment, **no nesting** |
| Java `synchronized` | nesting-raising (§10) | `Z-SYNCHRONIZED`: no increment, **no nesting** |
| boolean operators | **+1 per operator token** (`n−1` for an `n`-operand chain) | **+1 per maximal sequence** |
| recursion | **+0** — call resolution unavailable | **+1** by lexical name match, limits stated below |
| `else` | **+0** | **+1** (`F-ELSE`) |
| nesting semantics | structural depth, `try`/`finally`/`with`/case bodies raise it | comprehension load; `finally` raises, `with`/`synchronized` do not |

## 4. Stated limits of `F-RECURSION`

ArchLens resolves no calls. `F-RECURSION` fires on a call whose callee name
matches the enclosing callable's own name, lexically. Therefore:

* a shadowed local of the same name is a **false positive**;
* an overload or a same-named method on a different receiver is a **false
  positive**;
* mutual recursion (`f` calls `g` calls `f`) is a **false negative**.

These are published with the metric, not discovered later. The alternative —
omitting recursion — would diverge from three of the four reference
implementations.

---

## 5. Applicability matrix

`•` applicable · `—` the language has no such construct.

| Rule | Java | Go | JavaScript | TypeScript | Python |
|---|:--:|:--:|:--:|:--:|:--:|
| `S-IF` | • | • | • | • | • |
| `S-LOOP` | • | • | • | • | • |
| `S-SWITCH` | • | • | • | • | • (`match`) |
| `S-SELECT` | — | • | — | — | — |
| `S-CATCH` | • | — | • | • | • |
| `S-TERNARY` | • | — | • | • | • |
| `S-COMPREHENSION` | — | — | — | — | • |
| `F-ELSEIF` | • | • | • | • | • |
| `F-ELSE` | • | • | • | • | • |
| `F-LOOPELSE` | — | — | — | — | • |
| `F-TRYELSE` | — | — | — | — | • |
| `F-BOOLSEQ` | • | • | • | • | • |
| `F-GUARD` | • (JDK 21) | — | — | — | • |
| `F-COMPIF` | — | — | — | — | • |
| `F-LABELJUMP` | • | • | • | • | — |
| `F-RECURSION` | • | • | • | • | • |
| `N-FINALLY` | • | — | • | • | • |
| `Z-TRY` | • | — | • | • | • |
| `Z-JUMP` | • | • | • | • | • |
| `Z-OPTCHAIN` | — | — | • | • | — |
| `Z-SYNCHRONIZED` | • | — | — | — | — |
| `Z-WITH` | — | — | — | — | • |
| `Z-DEFER` | — | • | — | — | — |
| `Z-ASSERT` | — | — | — | — | • |
| `Z-TYPEONLY` | — | — | — | • | — |
| `Z-BITWISE` | • | • | • | • | • |
| `B-NESTED` | • | • | • | • | • |

**Go has no `catch`, no ternary and no labelled-jump-free story**: `S-CATCH`,
`S-TERNARY` and `N-FINALLY` are marked `—` for Go and the corpus does not invent
them. `F-LABELJUMP` is marked `—` for Python, which has no labelled jump. Every
other cell is exercised by at least one corpus case in that language.

Two cells needed a decision rather than a lookup, and both are recorded as
decisions in §3: Java `synchronized` and Python `with` are zero **and**
non-nesting, which is what the reference implementations do and what the
cognitive model's rationale supports, even though CX nests both.
