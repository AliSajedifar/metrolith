"""Command handlers for the ``metrolith`` CLI.

Plan section 20. The public entry point stays ``metrolith = pipeline:main``, but
implementation lives here rather than accumulating in ``pipeline.py``.
``pipeline.py`` assembles the parser and delegates; it holds no artifact-reader
or renderer implementation.

Each module in this package owns one subcommand and exposes two callables:

``add_parser(subparsers)``
    Register the subcommand's arguments.
``handle(args) -> int``
    Execute it and return a process exit code.

Handlers are imported lazily by ``pipeline.py`` so that an unrelated subcommand
never pays the import cost of, say, the schema registry.
"""

from __future__ import annotations

__all__ = ["schema_command"]
