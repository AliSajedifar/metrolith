# ArchLens duplication engine v1 — D3-A structural contract

Status: frozen D3-A contract

Canonicalization version: `structural-v1`

Program version: `3.8.0`

## 1. Scope and equality

D3-A accepts only a complete, admitted D1 candidate produced from the existing
selected-syntax seam. It transforms that candidate into canonical structural
bytes and a deterministic fingerprint. Two candidates are structurally equal
if and only if their complete canonical byte strings are byte-for-byte equal.
The fingerprint is an index and is never the equality oracle.

D3-A does not group candidates, suppress nesting, create duplicate groups,
persist data, define an Artifact schema, expose a CLI or report, or integrate
with Policy, SARIF, GitHub Actions, or Hotspots.

The canonical root is a synthetic ordered `statement-sequence`. A callable
signature, branch condition, loop header, surrounding braces, and other
container material outside the admitted D1 span are not part of that sequence.
Every complete statement subtree inside the span is part of it.

## 2. Retained and abstracted information

Structural canonicalization retains:

- the detected language domain;
- every semantic node kind;
- the parent-child field or positional edge;
- child and statement ordering;
- grammar-significant node, edge, and list boundaries;
- exact operator identity;
- exact modifier and semantic-keyword identity;
- identifier role;
- candidate-local, within-role identifier equality pattern;
- literal class;
- f-string and template interpolation structure, including expressions,
  conversions, format specifications, substitutions, and tagged-template
  shape;
- regex and language-specific literal categories.

It abstracts:

- source coordinates, byte offsets, paths, and line numbers;
- formatting, whitespace, comments, and line-ending spelling;
- identifier spelling;
- literal values and lexical spelling;
- non-semantic separators or delimiters when the selected AST already
  represents their structure.

The contract explicitly forbids:

- sorting children or statements;
- commutative rewrites;
- expression reordering;
- associative flattening;
- constant folding;
- type resolution;
- name binding or symbol resolution;
- semantic equivalence claims;
- control-flow normalization;
- normalizing equivalent source syntax into one representation.

Consequently `a + b` does not match `b + a`, `x && y` does not match
`y && x`, and `+` never matches `-` unless the resulting canonical bytes are
already identical under these rules. No adapter is permitted to make those
bytes identical through a rewrite.

## 3. Identifier normalization

Each identifier is classified syntactically into exactly one D0 role:

- `value`: ordinary bindings, parameters, locals, declarations, and
  references;
- `type`: declared or referenced type names visible from grammar context;
- `member`: fields, properties, selector members, method members, keyword
  member names, and JSX member/tag/attribute names;
- `label`: statement labels and their syntactic targets.

Namespaces are separate by role. In canonical preorder, the first distinct
exact spelling in a role receives unsigned ordinal 0, the next receives 1, and
so on. A repeated spelling in that role reuses its ordinal. The canonical leaf
contains `(identifier, role, ordinal)` and never the spelling. Unicode spelling
is compared as decoded Unicode scalars without normalization or case-folding
before the ordinal is assigned.

Thus a consistent rename may match:

```text
total  = price + tax
amount = cost  + fee
```

but a changed equality pattern does not:

```text
total  = price + tax
amount = cost  + cost
```

No symbol table is built. A runtime call target is not inferred to be a type,
a same-spelling shadowed variable is not separated by binding, and a renamed
external member can match another member. These are known false-positive
risks, especially with shadowing, dynamic dispatch, reflection, monkey
patching, external APIs, and names whose runtime role differs from their
grammar role. Structural equality must never be described as semantic identity
or equal runtime behavior.

## 4. Literal normalization

Literal values are replaced by a class token; the containing AST shape remains.

| Language | Retained classes |
|---|---|
| Python | `none`, `boolean`, `integer`, `float`, `complex`, `string`, `bytes`, `ellipsis`; `JoinedStr`/`FormattedValue` structure |
| Java | `null`, `boolean`, `char`, `string`, `text_block`, `integer`, `long_integer`, `float`, `double` |
| Go | `nil`, `boolean`, `integer`, `floating`, `imaginary`, `rune`, `interpreted_string`, `raw_string` |
| JavaScript/TypeScript | `null`, `boolean`, `number`, `bigint`, `string`, `regex`, and template structure |

`10` may therefore match `20`, while `10` cannot match `"10"`. Python
f-string raw chunks and JavaScript/TypeScript template raw chunks are
abstracted, but their ordered interpolation nodes and full expression subtrees
are retained. Regex values are abstracted only after retaining the regex class
and enclosing grammar shape. Tagged and untagged templates retain distinct
parent structure. Literal shape is never erased into one universal token.

## 5. Operators, modifiers, and tree structure

Python operator subclasses are emitted as semantic node kinds. For Java, Go,
JavaScript, and TypeScript, the selected tree-sitter grammar's semantic
operator/modifier children are emitted in their original parent edge and child
order. This includes assignment, arithmetic, comparison, boolean, bitwise,
shift, unary, update, spread/rest, channel, arrow, optional-chain,
async/generator, await/yield, Java modifier, Go `defer`/`go`, and TypeScript
operator/modifier distinctions characterized by D0 revision 1.

Anonymous grammar tokens not classified as a non-semantic delimiter, an exact
semantic symbol, or a grammar keyword make the candidate unavailable. An
unknown identifier leaf, malformed/missing node, invalid UTF-8 leaf, ambiguous
statement sequence, or unsupported Python string field also makes it
unavailable. Operators are never dropped, and source scanning is never used as
a recovery heuristic.

## 6. Language adapters and parser authority

The only adapters are:

- Python: the CPython AST selected by `modules.source_frontend.select_syntax`;
- Java: the selected `tree-sitter-java` tree;
- Go: the selected `tree-sitter-go` tree;
- JavaScript: the selected `tree-sitter-javascript` tree, including an aligned
  existing compatibility selection;
- TypeScript/TSX: the selected `tree-sitter-typescript` tree, including an
  aligned existing compatibility selection.

D3-A never parses source independently and never creates another parser path.
The candidate language must equal the selected-syntax language, its span must
be inside the byte-aligned selected source, the selected syntax must be eligible
for duplication, and the candidate must have passed all D1 floors.

An unrepresentable candidate produces `StructuralCanonicalizationResult` with
status `unavailable`, a `StructuralUnavailableReason`, no canonical bytes, and
no fingerprint. It is not a zero, an empty structure, or a guessed result.

## 7. Canonical bytes

The byte stream begins with:

```text
ARCHLENS-DUPLICATION-STRUCTURAL\0
frame("structural-v1")
frame(detected_language)
```

Every subsequent tag and value is framed as:

```text
unsigned-64-bit big-endian byte length || exact bytes
```

Text values are UTF-8. Canonical events use distinct
`statement-sequence:start/end`, `node:start/end`, `edge:start/end`, and
`list:start/end` tags. Identifier ordinals are unsigned 64-bit big-endian data.
No `repr`, native hash, source coordinate, path, locale, dictionary iteration,
or platform-dependent value enters the stream.

Changing any retained rule, adapter mapping, event framing, magic, or version
requires a new structural fingerprint version. A Program version change alone
does not authorize reinterpretation of `structural-v1`.

## 8. Fingerprint and collision rule

The deterministic index is:

```text
payload = frame("archlens-duplication-fingerprint")
        + frame("structural-v1")
        + frame(detected_language)
        + frame(canonical_bytes)

fingerprint = "sha256:" + lowercase_hex(SHA-256(payload))
```

The canonical header version and language must agree with the fingerprint
request. Digest implementations must return exactly 32 bytes. Equal canonical
bytes in the same language have equal fingerprints. Equal fingerprints do not
establish structural equality: a later grouping stage must split every digest
bucket by complete canonical-byte equality. D3-A itself performs no grouping.

## 9. Validation authority and non-claims

The independent oracle under `validation/duplication_d3a_20260818/` does not
import the production structural canonicalizer, structural fingerprint, or any
production grouping implementation. It independently validates canonical
bytes, equality relations, role namespaces, literal classes, and operator/order
retention against the synthetic corpus and small normative vectors.

Structural equality is syntactic Type-2 equality under this contract. It is not
proof of equivalent values, bindings, types, side effects, control flow,
security properties, or runtime behavior. D3-B grouping, nesting, IDs, and
output remain out of scope.

Navigation: [usage](USAGE.md) · [project README](../README.md).
