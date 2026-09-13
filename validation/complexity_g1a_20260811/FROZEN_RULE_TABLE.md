# ArchLens Cognitive Complexity — frozen rule table (G1-A)

**Revision 3 — owner-review closure, 2026-08-11.**

## Amendment log

Freezing is not immutability; it is that changes are **visible**. Every
amendment is listed here, every superseded revision is kept byte-for-byte, and
no revision is ever edited in place.

| # | Revision | Change | Authority |
|---|---|---|---|
| 001 | 1 → 2 | **`N-FINALLY` becomes `Z-FINALLY`**: a `finally` block contributes **0** and raises **no** nesting. It is ignored entirely | Owner decision, 2026-08-11 |
| 002 | 2 → 3 | **Owner-review closure.** Rule-count correction; `F-BOOLSEQ` made mechanical; `F-RECURSION` narrowed to a high-precision heuristic capped at +1 per callable; `B-NESTED` split into discovery vs attribution; precedence and default-traversal semantics added; Python comprehension semantics frozen | Owner decision, 2026-08-11 |

| Revision | File | sha256 | Bytes |
|---|---|---|---:|
| 1 | `FROZEN_RULE_TABLE.r1.md` | `89a82caf99f2a3193d0d6d95937b44299f52fdc7f43954621b0d3aba6bad5eb7` | 8 051 |
| 2 | `FROZEN_RULE_TABLE.r2.md` | `d50ba7fc8b6b7bfbb0a8dd1531bdf0dc81a3ab16ee94279b9229fe3856c7ff5b` | 10 215 |
| 3 | this file | recorded in `REVISION_HASHES.txt` after writing | — |

**Amendment 001 is unchanged by Amendment 002.** `Z-FINALLY` stays exactly as
revision 2 left it, and `N-FINALLY` is not restored.

### Correction notice — the "39 rules" claim

Revision 2's amendment note said "Rule count is unchanged at 39". **That number
was unsupported and is withdrawn.** Mechanically enumerated from this table
(§7), the real quantities are:

| Quantity | Value |
|---|---:|
| **Unique normative rule IDs** | **27** |
| Applicability cells (rule × language marked applicable) | 84 |
| Corpus callables pinning them | see `G1A_RECORD.md` |

39 corresponds to none of these. It was an assertion, not a count, and the
correction is recorded here rather than by quietly editing the earlier text —
revision 2's bytes are preserved with the wrong number in them, which is the
point of keeping them.

---

This is the owner-accepted rule table from
`validation/complexity_g0_20260811/G0_RECORD.md` §3, turned into identified,
individually addressable rules so a corpus case can cite the exact rule it pins.

**This is NOT a contract document.** Complexity Contract 2.0.0 does not exist and
is not created here. Nothing in this file is persisted by any artifact, no
production code implements it, and Complexity Contract 1.0.0 is untouched.

**Naming.** ArchLens Cognitive Complexity. Never "Sonar Cognitive Complexity"
and never "Sonar-compatible" — G0 established that the Sonar-lineage tools do
not agree with each other.

---

## 1. The four kinds of increment

Every rule below is exactly one of these. A callable starts at **0**.

| Kind | Prefix | Contribution | Raises nesting for its contents |
|---|---|---|---|
| **structural** | `S-` | `1 + nesting_level` | yes, unless the rule says otherwise |
| **flat** | `F-` | `1` | no |
| **nesting-only** | `N-` | `0` | yes |
| **zero** | `Z-` | `0` | no |
| **boundary** | `B-` | — | — (excludes a subtree entirely) |

`nesting_level` is the count of enclosing nesting-raising constructs **inside
the measured callable**. The callable's own body is level 0.

The distinction between *flat* and *structural* is the whole model: a construct
that adds a branch you must hold in your head scales with how deeply it is
buried; a construct that merely adds a term to a condition does not.

**The nesting-only kind is currently RESERVED AND EMPTY.** `N-FINALLY` was its
only member and became `Z-FINALLY` under Amendment 001. The kind is retained
because a future rule could legitimately be one; a reader should not infer that
a rule is missing.

---

## 2. Rules

### 2.1 Structural (7)

| ID | Construct | Contribution | Nesting |
|---|---|---|---|
| `S-IF` | `if` | `1 + n` | +1 for the branch body |
| `S-LOOP` | `for`, `for..in`, `for..of`, `for..range`, `while`, `do..while` | `1 + n` | +1 for the body |
| `S-SWITCH` | `switch` statement, switch expression, Go type switch, Python `match` | `1 + n` for the **whole construct** | +1 for the body |
| `S-SELECT` | Go `select` | `1 + n` | +1 for the body |
| `S-CATCH` | `catch` / `except` clause | `1 + n` | +1 for its body |
| `S-TERNARY` | conditional expression, incl. Python `a if c else b` | `1 + n` | +1 for both branches |
| `S-COMPREHENSION` | one Python comprehension or generator expression — see §5 | `1 + n` | +1 for its clauses and element expression |

### 2.2 Flat (9)

| ID | Construct | Contribution |
|---|---|---|
| `F-ELSEIF` | `else if` / `elif` | `1` |
| `F-ELSE` | bare `else` | `1` |
| `F-LOOPELSE` | Python `for`/`while` `else` | `1` |
| `F-TRYELSE` | Python `try…else` | `1` |
| `F-BOOLSEQ` | one maximal same-operator short-circuit sequence — see §3 | `1` per sequence |
| `F-GUARD` | Java `case X when g`, Python `case P if g` | `1` |
| `F-COMPIF` | Python comprehension `if` clause | `1` |
| `F-LABELJUMP` | labelled `break` / `continue`, Go `goto` | `1` |
| `F-RECURSION` | direct self-recursion — see §4 | `1` **per callable, at most once** |

`F-ELSEIF` and `F-ELSE` raise nesting **for their own body only**, not for their
siblings: an `if / else if / else` chain measures 3 and leaves the chain flat.

### 2.3 Nesting-only (0 — reserved, currently empty)

See §1. No rule is currently of this kind.

### 2.4 Zero (10)

| ID | Construct | Why |
|---|---|---|
| `Z-TRY` | the `try` block itself | it introduces no branch; its `catch` does |
| `Z-FINALLY` | `finally` block | **Amendment 001.** Ignored entirely: no increment and no nesting. `finally` runs unconditionally, so it adds no branch to hold in mind, and every available reference implementation agrees |
| `Z-JUMP` | unlabelled `break` / `continue` / `return` | no target to hold in mind |
| `Z-OPTCHAIN` | JS/TS `?.` | one guarded access, not a branch |
| `Z-SYNCHRONIZED` | Java `synchronized` | not a branch, and **not nesting-raising** |
| `Z-WITH` | Python `with` | as `synchronized` |
| `Z-DEFER` | Go `defer` / `go` **statement** | the statement is not a branch. It does **not** imply a function literal: `defer f(a && b)` defers an ordinary call, and by §6 its argument expression is still traversed. When a literal *is* present, that literal is handled by `B-NESTED`, not by this rule |
| `Z-ASSERT` | Python `assert`, `raise`, `yield`, `await` | no branch |
| `Z-TYPEONLY` | TS type annotations, `as`, `!`, conditional **types**, overload signatures, decorators | types are not control flow. This is a **subtree exclusion** for type positions (§6), not an ordinary zero node: a runtime expression must never be counted from inside a type |
| `Z-BITWISE` | `&`, `\|` and Java multi-catch `A \| B` | not short-circuit operators |

### 2.5 Boundary (1)

| ID | Rule |
|---|---|
| `B-NESTED` | Nested callable boundary. **See §7 — discovery and attribution are different traversals**, and the rule governs only the second |

---

## 3. `F-BOOLSEQ`, made mechanical

### 3.1 The operator sets, frozen

| Language | Operators |
|---|---|
| Java | `&&`, `\|\|` |
| Go | `&&`, `\|\|` |
| Python | `and`, `or` |
| JavaScript / TypeScript | `&&`, `\|\|`, `??` |

Nothing else is an `F-BOOLSEQ` operator. Bitwise `&`/`\|`, Java multi-catch
`A \| B`, TypeScript union types `A \| B`, unary `not`/`!` and Python chained
comparisons are all outside the set (`Z-BITWISE`, `Z-TYPEONLY`).

### 3.2 The definition

> A **boolean sequence** is a *maximal connected expression region using the
> same operator kind*. Each maximal same-operator sequence contributes exactly
> **+1, flat**. Changing operator kind starts a new sequence.
> **Parentheses alone do not start a new sequence.**

**Mechanically:** flatten the callable's short-circuit operators into **source
order**, then count maximal runs of the same operator. Parentheses are
transparent to the flattening — they never split a run. A *different* operator
appearing between two runs of the same operator **does** split them, so the
outer expression contributes two runs rather than one.

`a || b && c || d` therefore contributes **3**, not 2: the `&&` interrupts the
`||` run even though the two `||` nodes are parent and child in the tree. Tree
adjacency is the wrong reading and is stated here so an implementation does not
reach for it.

### 3.3 Pinned cases

| Expression | Sequences | Contribution |
|---|---:|---:|
| `a && b && c` | 1 | 1 |
| `a && b \|\| c` | 2 | 2 |
| `a && (b && c)` | **1** — parentheses do not split | 1 |
| `a && (b \|\| c)` | 2 | 2 |
| `a \|\| b && c \|\| d` | **3** — `\|\|`, then `&&`, then `\|\|` | 3 |
| `a ?? b ?? c` (JS/TS) | 1 | 1 |
| `a ?? b \|\| c` (JS/TS) | 2 | 2 |

### 3.4 Logical assignment operators are NOT `F-BOOLSEQ`

`&&=`, `\|\|=` and `??=` contribute **0**. The assignment operator itself is not
a boolean sequence operator.

Its child expressions remain traversable under §6: `x &&= (a \|\| b)` scores 1 —
zero for the `&&=`, one for the `\|\|` sequence in its right operand.

This is pinned by explicit JS/TS corpus cases so it can never be inferred from
implementation behaviour.

**This rule is not adjusted to agree with SonarJS.** SonarJS 4.2.0 scores `\|\|`
and `??` sequences at zero (verified in its shipped source); that remains a
`DefinitionMapping` difference (D6, D7), not a reason to change the definition.

---

## 4. `F-RECURSION`, narrowed to a high-precision heuristic

### 4.1 Cap

> `F-RECURSION` contributes **at most +1 per measured callable**, regardless of
> how many recursive call sites appear.

### 4.2 What is recognized

Only syntactically plausible **direct self-calls**:

**A. Module or free function** — a bare callee identifier equal to the current
callable's name: `f(...)` inside `f`.

**B. Instance / class method** — a member name equal to the current callable's
name **and** a receiver that is one of:

* the language's explicit current receiver — `this` (Java, JS/TS), `self` /
  `cls` (Python), the **declared receiver identifier** (Go);
* the current enclosing type name, where a static or class-qualified self-call
  is syntactically explicit — `Foo.bar(...)` inside `Foo.bar`.

### 4.3 What is NOT recognized

`arbitraryObject.sameName()` is **not** recursion merely because the member name
matches. A call on any receiver outside §4.2 B is ignored.

### 4.4 Stated limits, unchanged in kind

ArchLens resolves no calls. Therefore:

* an **alias** (`const g = f; g()`) is a false negative;
* **mutual recursion** (`f` → `g` → `f`) is not detected;
* **dynamic dispatch** is unresolved — an overridden method reached through a
  base-typed receiver is neither confirmed nor excluded;
* a shadowed local of the same name called as a bare identifier remains a
  possible false positive under rule A, which is the residual cost of having no
  symbol table.

Narrowing from revision 2's "callee name equals current callable name" removes
the largest false-positive class — same-named methods on unrelated receivers —
at the cost of the alias false negative, which is rarer and quieter.

### 4.5 Reference behaviour, measured 2026-08-11

Probed with `f(){f();}`, `f(){f();f();}`, `f(){f();f();f();}` and receiver forms:

| Reference | 1 call | 2 calls | 3 calls | via explicit receiver | same name, other receiver |
|---|---:|---:|---:|---|---|
| PMD 7.7.0 | 1 | **2** | **3** | `this.viaThis()` → **1** | not counted ✓ |
| gocognit 1.2.1 | 1 | **2** | **3** | receiver self-call → **0** | not counted ✓ |
| `cognitive_complexity` 1.3.0 | 1 | **1** | **1** | `self.m()` → **0** | not counted ✓ |
| eslint-plugin-sonarjs 4.2.0 | 0 | 0 | 0 | 0 | 0 |

**PMD and gocognit count per call site; the Python package counts per callable;
SonarJS does not count recursion at all.** The owner decision stands at +1 per
callable, so three new divergences are recorded rather than the rule being
changed (D22, D23, D24 — see `G1A_RECORD.md`).

---

## 5. Python comprehension semantics, frozen

> A **single comprehension expression** contributes exactly **one**
> `S-COMPREHENSION` structural increment, regardless of how many generator
> clauses it has.

* Additional `for` clauses in the **same** comprehension add **nothing**.
* Each comprehension `if` clause contributes its own `F-COMPIF` **+1**.
* Boolean sequences inside a comprehension condition are **independently
  additive** under §3 and §6.
* A genuinely **nested** comprehension expression is a separate
  `S-COMPREHENSION` and observes the nesting created by the outer one.

### Pinned cases

| Source | Increments | Total |
|---|---|---:|
| `[x for x in xs]` | `S-COMPREHENSION` @0 | **1** |
| `[x for x in xs for y in ys]` | `S-COMPREHENSION` @0; second `for` adds 0 | **1** |
| `[x for x in xs for y in ys if p(y)]` | `S-COMPREHENSION` @0 + `F-COMPIF` | **2** |
| `[x for x in xs if a and b]` | `S-COMPREHENSION` @0 + `F-COMPIF` + `F-BOOLSEQ` | **3** |
| `[[y for y in ys] for x in xs]` | outer `S-COMPREHENSION` @0 = 1; inner `S-COMPREHENSION` @1 = **2** | **3** |

---

## 6. Precedence and default traversal

### 6.1 Precedence — specialized rules suppress overlapping generic ones

| Construct | Fires | Does **not** also fire |
|---|---|---|
| `else if` / `elif` | `F-ELSEIF` | `F-ELSE` + `S-IF` |
| Python loop `else` | `F-LOOPELSE` | `F-ELSE` |
| Python `try…else` | `F-TRYELSE` | `F-ELSE` |
| comprehension `if` clause | `F-COMPIF` | `S-IF`, `S-TERNARY` |
| `case`/`match` guard | `F-GUARD` | `S-IF` |

**Precedence suppresses the overlapping rule, never the operands.** A
comprehension `if` clause and a `case` guard each fire once, *and* their
condition expressions are still scanned for `F-BOOLSEQ` sequences, which are
independently additive.

### 6.2 Default traversal

> A syntax construct with **no explicit cognitive increment rule** contributes
> `increment = 0` and `nesting increment = 0`, **but its children are still
> traversed** for independently countable constructs — unless an explicit
> boundary or subtree-exclusion rule says otherwise.

Worked examples:

| Source | Reading |
|---|---|
| `return (a ? b : c)` | `return` 0; the **ternary still counts** |
| `defer f(a && b)` (Go) | `defer` 0; the **boolean sequence still counts** |
| `x &&= (a \|\| b)` (JS/TS) | `&&=` 0; the **`\|\|` sequence still counts** |
| `throw new E(a && b)` | `throw` 0; the sequence still counts |

### 6.3 Subtree exclusions are exclusions, not zero nodes

Two rules remove a subtree from attribution entirely, and must not be
implemented as ordinary zero-contribution nodes:

* **`B-NESTED`** — a nested callable's body (§7);
* **`Z-TYPEONLY`** — type positions, so a runtime-looking expression written
  inside a type never contributes.

---

## 7. `B-NESTED` — discovery is not attribution

Two traversals exist and they are not the same traversal. Conflating them is
what the wording of revisions 1 and 2 risked.

### 7.1 Global discovery

> Traverse **through** enclosing lexical scopes as far as needed to discover
> every callable belonging to the canonical `methods_functions` population.
> Discovery is global: a measurable callable is found wherever it is written.

A method of a class declared inside a function **is discovered** and **is
measured**, in every language where that is expressible.

### 7.2 Current-callable attribution

> While measuring **one** callable, stop structural, boolean and recursion
> attribution at every nested callable boundary. A nested callable contributes
> **nothing** to its enclosing callable — not its increments and not its
> nesting.

### 7.3 The two outcomes for a nested callable

| The nested callable is… | Result |
|---|---|
| in the canonical population | it receives its **own independent measurement row**, starting at 0 with nesting 0 |
| not in the canonical population | its body contributes to **no** Cognitive Complexity row at all |

The second is a coverage limitation, stated rather than hidden, and it is
identical to Complexity Contract 1.0.0 §5.2 — deliberately, so both metrics
measure one population.

### 7.4 Pinned discovery cases

| Language | Shape | Must be discovered and measured |
|---|---|---|
| Python | `def` → `class` → method | yes |
| Java | method → **named local class** → method | yes |
| JavaScript / TypeScript | `function` → `class` → method | yes |

### 7.5 Pinned non-leakage cases

A lambda, an arrow/callback, an anonymous-class method, a Go `func_literal` and
a nested `def` outside the canonical population each contribute **nothing** to
the enclosing callable.

---

## 8. Deliberate divergences from Complexity Contract 1.0.0

Recorded because a reader will otherwise assume the two agree on the same file.

| Construct | CX (Cyclomatic 1.0.0) | CG (Cognitive, frozen here) |
|---|---|---|
| `switch` | **+1 per arm** | **+1 for the whole construct**, arms 0 |
| Python `with` | nesting-raising (§10) | `Z-WITH`: no increment, no nesting |
| Java `synchronized` | nesting-raising (§10) | `Z-SYNCHRONIZED`: no increment, no nesting |
| `finally` | nesting-raising | `Z-FINALLY`: ignored entirely (Amendment 001) |
| boolean operators | **+1 per operator token** (`n−1` for an `n`-operand chain) | **+1 per maximal same-operator sequence** (§3) |
| recursion | **+0** — call resolution unavailable | **+1 per callable, at most once** (§4) |
| `else` | **+0** | **+1** (`F-ELSE`) |
| Python comprehension | +1 per `for` clause and +1 per `if` clause | **one** `S-COMPREHENSION` per comprehension, +1 per `if` clause (§5) |

---

## 9. Applicability matrix

`•` applicable · `—` the language has no such construct.

| Rule | Java | Go | JavaScript | TypeScript | Python |
|---|:--:|:--:|:--:|:--:|:--:|
| `S-IF` | • | • | • | • | • |
| `S-LOOP` | • | • | • | • | • |
| `S-SWITCH` | • | • | • | • | • |
| `S-SELECT` | — | • | — | — | — |
| `S-CATCH` | • | — | • | • | • |
| `S-TERNARY` | • | — | • | • | • |
| `S-COMPREHENSION` | — | — | — | — | • |
| `F-ELSEIF` | • | • | • | • | • |
| `F-ELSE` | • | • | • | • | • |
| `F-LOOPELSE` | — | — | — | — | • |
| `F-TRYELSE` | — | — | — | — | • |
| `F-BOOLSEQ` | • | • | • | • | • |
| `F-GUARD` | • | — | — | — | • |
| `F-COMPIF` | — | — | — | — | • |
| `F-LABELJUMP` | • | • | • | • | — |
| `F-RECURSION` | • | • | • | • | • |
| `Z-TRY` | • | — | • | • | • |
| `Z-FINALLY` | • | — | • | • | • |
| `Z-JUMP` | • | • | • | • | • |
| `Z-OPTCHAIN` | — | — | • | • | — |
| `Z-SYNCHRONIZED` | • | — | — | — | — |
| `Z-WITH` | — | — | — | — | • |
| `Z-DEFER` | — | • | — | — | — |
| `Z-ASSERT` | — | — | — | — | • |
| `Z-TYPEONLY` | — | — | — | • | — |
| `Z-BITWISE` | • | • | • | • | • |
| `B-NESTED` | • | • | • | • | • |

**27 rules; 84 applicable cells.** Both are mechanically enumerated by
`tests/test_cognitive_corpus_g1a.py`, which parses this matrix rather than
restating it.

Go has no `catch`, no ternary and no `finally`; Python has no labelled jump.
Those cells are `—` and the corpus does not invent them.
