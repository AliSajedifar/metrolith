"""Lizard as an EXTERNAL cyclomatic reference, driven by a listing file.

Lizard is not a primary adapter and not ground truth. It implements *its own*
cyclomatic definition over its own per-language tokenizers, so a difference
against ArchLens is a property of two definitions until adjudicated, and is
never on its own grounds for changing ArchLens.

Why this wrapper exists rather than the `lizard` command line:

* **The command line is not a supported input channel here.** A real subject
  holds hundreds to thousands of source files and the joined command line
  exceeds the Windows limit, failing as an opaque ``WinError 206``. One UTF-8
  path per line has no such limit.
* **A file Lizard could not read must not look like a file with no callables.**
  ``lizard.analyze_file`` swallows ``IOError`` and ``UnicodeDecodeError``,
  writes a line to stderr and returns ``FileInformation(filename, 0, [])`` --
  indistinguishable from a genuinely function-free file. Verified by reading
  ``FileAnalyzer.__call__`` in Lizard 1.17.31. This wrapper therefore performs
  the read itself and calls ``analyze_source_code`` directly, so a read or
  decode failure becomes ``read_by_lizard: false`` with a reason instead of a
  zero. It also records when no tokenizer claims the extension at all.

Runs under the pinned reference interpreter, never under the ArchLens runtime.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

import lizard

#: Versioned independently of Lizard itself: this wrapper's *contract* can
#: change while the tool stays pinned, and a reader must be able to tell which
#: moved.
WRAPPER_VERSION = "1.0.0"


def _language_of(path: str) -> str | None:
    """The reader Lizard resolves for this path, or None if it claims none."""
    for reader in lizard.languages():
        if reader.match_filename(path):
            return reader.language_names[0]
    return None


def _analyze(path: str) -> dict[str, Any]:
    language = _language_of(path)
    if language is None:
        return {
            "path": path,
            "read_by_lizard": False,
            "reason": "no Lizard tokenizer claims this file extension",
            "callables": [],
        }
    # The read is performed HERE, not by `analyze_file`, which would turn an
    # unreadable file into an empty function list.
    try:
        source = lizard.auto_read(path)
    except Exception as error:  # noqa: BLE001 - the reason is the payload
        return {
            "path": path,
            "read_by_lizard": False,
            "reason": f"read failed: {type(error).__name__}: {error}",
            "callables": [],
        }
    try:
        analysis = lizard.analyze_file.analyze_source_code(path, source)
    except Exception as error:  # noqa: BLE001 - the reason is the payload
        return {
            "path": path,
            "read_by_lizard": False,
            "reason": f"tokenizer failed: {type(error).__name__}: {error}",
            "callables": [],
        }

    callables = [
        {
            # Lizard names callables its own way (`Type::method`,
            # `name_in_space`). Both spellings are emitted so the matcher can
            # use whichever the language's reader produced, and the span
            # remains available when neither spelling matches ArchLens.
            "qualified_name": item.name,
            "long_name": item.long_name,
            "name_in_space": item.name_in_space,
            "signature_discriminator": None,
            "start_line": item.start_line,
            "end_line": item.end_line,
            "cyclomatic_complexity": item.cyclomatic_complexity,
            "nloc": item.nloc,
            "formal_parameter_count": item.parameter_count,
        }
        for item in analysis.function_list
    ]
    return {
        "path": path,
        "read_by_lizard": True,
        "lizard_language": language,
        "callables": callables,
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--list", dest="listing", required=True,
        help="UTF-8 file holding one source path per line",
    )
    arguments = parser.parse_args(argv)

    with open(arguments.listing, encoding="utf-8") as handle:
        paths = [line.strip() for line in handle if line.strip()]

    json.dump(
        {
            "wrapper_version": WRAPPER_VERSION,
            "lizard_version": lizard.version,
            "files": [_analyze(path) for path in paths],
        },
        sys.stdout,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
