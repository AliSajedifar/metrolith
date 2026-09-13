"""Materialize the frozen D2 lexical-exact synthetic corpus.

The byte-level portability fixtures cannot be represented faithfully by a
line-oriented patch: this builder deliberately writes UTF-8 BOM, CRLF, and
lone-CR sources.  It has no production-module imports and is safe to rerun.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "corpus"

NAMES = (
    "alpha",
    "beta",
    "gamma",
    "delta",
    "epsilon",
    "zeta",
    "eta",
    "theta",
    "iota",
)


def _statements(language: str) -> list[str]:
    if language == "Python":
        return [f"{name} = source + {index}" for index, name in enumerate(NAMES, 1)]
    if language == "Java":
        return [
            f"int {name} = source + {index};"
            for index, name in enumerate(NAMES, 1)
        ]
    if language == "Go":
        return [f"{name} := source + {index}" for index, name in enumerate(NAMES, 1)]
    return [
        f"const {name} = source + {index};"
        for index, name in enumerate(NAMES, 1)
    ]


def _wrapper(language: str, label: str, statements: list[str], *, mode: str) -> str:
    if mode == "whitespace":
        statements = [
            statement.replace(" = ", "\t=\t").replace(" + ", "  +  ")
            for statement in statements
        ]
        statements = [statement.replace(" := ", "\t:=\t") for statement in statements]
        statements = [statement.replace("int ", "int\t") for statement in statements]
        statements = [statement.replace("const ", "const\t") for statement in statements]
    elif mode == "comment":
        marker = "#" if language == "Python" else "//"
        statements = [
            value
            for index, statement in enumerate(statements)
            for value in (
                f"{marker} comment {index + 1}",
                f"{statement}  {marker} trailing {index + 1}",
            )
        ]

    if language == "Python":
        indent = "  " if mode == "whitespace" else "    "
        return f"def {label}(source):\n" + "".join(
            f"{indent}{statement}\n" for statement in statements
        )
    if language == "Java":
        class_name = "Fixture" + "".join(part.title() for part in label.split("_"))
        return (
            f"class {class_name} {{\n"
            f"  static void {label}(int source) {{\n"
            + "".join(f"    {statement}\n" for statement in statements)
            + "  }\n}\n"
        )
    if language == "Go":
        return (
            "package corpus\n\n"
            f"func {label}(source int) {{\n"
            + "".join(f"\t{statement}\n" for statement in statements)
            + "}\n"
        )
    if language == "JavaScript":
        return (
            f"function {label}(source) {{\n"
            + "".join(f"  {statement}\n" for statement in statements)
            + "}\n"
        )
    return (
        f"function {label}(source: number): void {{\n"
        + "".join(f"  {statement}\n" for statement in statements)
        + "}\n"
    )


def _variant(language: str, mode: str) -> list[str]:
    statements = _statements(language)
    if mode == "identifier":
        statements[0] = statements[0].replace("alpha", "renamed", 1)
    elif mode == "literal":
        statements[0] = statements[0].replace("1", "101", 1)
    elif mode == "operator":
        statements[0] = statements[0].replace("+", "-", 1)
    elif mode == "reordered":
        statements[0], statements[1] = statements[1], statements[0]
    elif mode == "added":
        if language == "Java":
            statements.append("int kappa = source + 10;")
        elif language == "Go":
            statements.append("kappa := source + 10")
        elif language in {"JavaScript", "TypeScript"}:
            statements.append("const kappa = source + 10;")
        else:
            statements.append("kappa = source + 10")
    elif mode == "removed":
        statements.pop()
    return statements


def _write(relative: str, text: str, *, bom: bool = False, newline: bytes = b"\n") -> None:
    path = CORPUS / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = text.replace("\n", newline.decode("ascii")).encode("utf-8")
    if bom:
        payload = b"\xef\xbb\xbf" + payload
    path.write_bytes(payload)


def build() -> None:
    extensions = {
        "Python": "py",
        "Java": "java",
        "Go": "go",
        "JavaScript": "js",
        "TypeScript": "ts",
    }
    directory = {
        "Python": "python",
        "Java": "java",
        "Go": "go",
        "JavaScript": "javascript",
        "TypeScript": "typescript",
    }
    for language, extension in extensions.items():
        folder = directory[language]
        for mode in (
            "base",
            "exact",
            "whitespace",
            "comment",
            "identifier",
            "literal",
            "operator",
            "reordered",
            "added",
            "removed",
        ):
            statements = _variant(language, mode)
            text = _wrapper(language, mode, statements, mode=mode)
            _write(f"{folder}/{mode}.{extension}", text)

    same_file = _wrapper("Python", "same_one", _statements("Python"), mode="base")
    same_file += "\n" + _wrapper(
        "Python", "same_two", _statements("Python"), mode="base"
    )
    _write("python/same_file.py", same_file)

    _write(
        "java/crlf.java",
        _wrapper("Java", "crlf", _statements("Java"), mode="base"),
        newline=b"\r\n",
    )
    _write(
        "go/lone_cr.go",
        _wrapper("Go", "lone_cr", _statements("Go"), mode="base"),
        newline=b"\r",
    )
    _write(
        "typescript/bom.ts",
        _wrapper("TypeScript", "bom", _statements("TypeScript"), mode="base"),
        bom=True,
    )

    unicode_body = [
        f"{name} = منبع + {index}"
        for index, name in enumerate(
            ("آلفا", "بتا", "گاما", "دلتا", "اپسیلون", "زتا", "اتا", "تتا", "یوتا"),
            1,
        )
    ]
    _write(
        "python/café/日本語_unicode_a.py",
        _wrapper("Python", "unicode_a", unicode_body, mode="base"),
    )
    _write(
        "python/café/日本語_unicode_b.py",
        _wrapper("Python", "unicode_b", unicode_body, mode="comment"),
    )
    nfc = _statements("Python")
    nfc[0] = "café = source + 1"
    nfd = _statements("Python")
    nfd[0] = "cafe\u0301 = source + 1"
    _write("python/unicode_nfc.py", _wrapper("Python", "nfc", nfc, mode="base"))
    _write("python/unicode_nfd.py", _wrapper("Python", "nfd", nfd, mode="base"))

    malformed = {
        "python/malformed.py": "def broken(:\n    pass\n",
        "java/malformed.java": "class Broken { void broken( {\n",
        "go/malformed.go": "package corpus\nfunc broken( {\n",
        "javascript/malformed.js": "function broken( {\n",
        "typescript/malformed.ts": "function broken(: number {\n",
    }
    for relative, text in malformed.items():
        _write(relative, text)


if __name__ == "__main__":
    build()
