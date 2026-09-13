"""Independent line classification: the reference LOC implementation.

**Independence.** This module imports nothing from ``modules`` and uses no
tokenizer, no parser and no ArchLens code. It is a character-level state machine
written from the definition, and it is deliberately a *different technology*
from every ArchLens mechanism it is compared against:

============  ==============================  ==========================
Language      ArchLens                        This reference
============  ==============================  ==========================
Python        stdlib ``tokenize``             character scanner
Java          tree-sitter-java                character scanner
JavaScript    tree-sitter-javascript          character scanner
TypeScript    tree-sitter-typescript          character scanner
Go            tree-sitter-go                  character scanner
============  ==============================  ==========================

**Definition implemented** (``loc.physical_code_lines.v1``): a physical line is
blank when it is whitespace-only; otherwise it is code when anything remains
after comments are masked, and a comment when nothing does. A line mixing code
and a trailing comment is code. Comment markers inside string literals are not
comments. Python docstrings are string expressions and therefore code.

This is ArchLens's definition, implemented independently. Holding the definition
fixed and varying the implementation is what makes a disagreement evidence about
one of the two implementations rather than about wording.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Per-language lexical syntax. Everything this scanner needs to know.
_SYNTAX = {
    "Python": {
        "line_comments": ("#",),
        "block_comments": (),
        "quotes": ('"""', "'''", '"', "'"),
        "escape": True,
    },
    "Java": {
        "line_comments": ("//",),
        "block_comments": (("/*", "*/"),),
        "quotes": ('"""', '"', "'"),
        "escape": True,
    },
    "JavaScript": {
        "line_comments": ("//",),
        "block_comments": (("/*", "*/"),),
        "quotes": ('"', "'", "`"),
        "escape": True,
    },
    "TypeScript": {
        "line_comments": ("//",),
        "block_comments": (("/*", "*/"),),
        "quotes": ('"', "'", "`"),
        "escape": True,
    },
    "Go": {
        "line_comments": ("//",),
        "block_comments": (("/*", "*/"),),
        # A backtick raw string in Go takes no escapes; handled below.
        "quotes": ('"', "'", "`"),
        "escape": True,
    },
}

SUPPORTED = tuple(sorted(_SYNTAX))


@dataclass(frozen=True)
class LineCounts:
    total_physical_lines: int
    blank_lines: int
    comment_lines: int
    code_lines: int

    @property
    def nonblank_lines(self) -> int:
        return self.comment_lines + self.code_lines

    def as_dict(self) -> dict[str, int]:
        return {
            "total_physical_lines": self.total_physical_lines,
            "blank_lines": self.blank_lines,
            "comment_lines": self.comment_lines,
            "code_lines": self.code_lines,
            "nonblank_lines": self.nonblank_lines,
        }


class UnsupportedLanguage(ValueError):
    """Raised rather than guessing at a language this scanner cannot lex."""


def mask_comments(text: str, language: str) -> str:
    """Replace comment characters with spaces, preserving line structure.

    Masking rather than deleting keeps every physical line at its original
    index, so line classification stays a simple positional comparison.
    """
    try:
        syntax = _SYNTAX[language]
    except KeyError:
        raise UnsupportedLanguage(
            f"no lexical syntax defined for {language!r}; known: "
            f"{', '.join(SUPPORTED)}"
        ) from None

    line_comments = syntax["line_comments"]
    block_comments = syntax["block_comments"]
    quotes = syntax["quotes"]

    out: list[str] = []
    index = 0
    length = len(text)
    while index < length:
        character = text[index]

        # A string literal. Consume it verbatim: nothing inside is a comment.
        matched_quote = next(
            (quote for quote in quotes if text.startswith(quote, index)), None
        )
        if matched_quote is not None:
            end = _consume_string(text, index, matched_quote, syntax["escape"])
            out.append(text[index:end])
            index = end
            continue

        # A block comment. Mask it, but keep its newlines so line numbers hold.
        opener = next(
            (pair for pair in block_comments if text.startswith(pair[0], index)),
            None,
        )
        if opener is not None:
            close = text.find(opener[1], index + len(opener[0]))
            end = length if close == -1 else close + len(opener[1])
            out.append(_blank_out(text[index:end]))
            index = end
            continue

        # A line comment. Mask to just before the newline.
        if any(text.startswith(marker, index) for marker in line_comments):
            newline = text.find("\n", index)
            end = length if newline == -1 else newline
            out.append(_blank_out(text[index:end]))
            index = end
            continue

        out.append(character)
        index += 1
    return "".join(out)


def _consume_string(text: str, start: int, quote: str, escapes: bool) -> int:
    """Return the index just past the closing quote (or end of text)."""
    # Go and JavaScript backtick strings never honour backslash escapes.
    honours_escapes = escapes and quote != "`"
    index = start + len(quote)
    length = len(text)
    while index < length:
        if honours_escapes and text[index] == "\\":
            index += 2
            continue
        if text.startswith(quote, index):
            return index + len(quote)
        # A single-quoted or double-quoted string does not span lines in any of
        # these languages; an unterminated one ends at the newline rather than
        # swallowing the rest of the file.
        if len(quote) == 1 and quote in ("'", '"') and text[index] == "\n":
            return index
        index += 1
    return length


def _blank_out(fragment: str) -> str:
    """Same length, same newlines, no content."""
    return "".join("\n" if character == "\n" else " " for character in fragment)


def classify(text: str, language: str) -> LineCounts:
    """Classify every physical line of one source file."""
    masked = mask_comments(text, language)
    raw_lines = text.splitlines()
    masked_lines = masked.splitlines()
    if len(masked_lines) < len(raw_lines):
        masked_lines.extend([""] * (len(raw_lines) - len(masked_lines)))

    blank = comment = code = 0
    for original, without_comments in zip(raw_lines, masked_lines):
        if not original.strip():
            blank += 1
        elif without_comments.strip():
            code += 1
        else:
            comment += 1
    return LineCounts(
        total_physical_lines=len(raw_lines),
        blank_lines=blank,
        comment_lines=comment,
        code_lines=code,
    )
