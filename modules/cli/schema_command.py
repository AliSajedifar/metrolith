"""``metrolith schema list`` and ``metrolith schema export`` (plan section 7.7).

Schemas are resolved through :mod:`importlib.resources`, so both subcommands
work identically from a source checkout and from an installed wheel.
"""

from __future__ import annotations

import json
from pathlib import Path

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2


def add_parser(subparsers) -> None:
    schema = subparsers.add_parser(
        "schema",
        help="List or export the packaged artifact JSON Schemas",
        description=(
            "List or export packaged Artifact JSON Schemas. Exit 0 = completed, "
            "1 = schema dependency/structure/export failure, and 2 = invalid usage."
        ),
    )
    schema_sub = schema.add_subparsers(dest="schema_command", required=True)

    listing = schema_sub.add_parser("list", help="List every packaged schema")
    listing.add_argument(
        "--format", choices=("text", "json"), default="text",
        help="Output format (default: text)",
    )

    export = schema_sub.add_parser("export", help="Write every packaged schema to a directory")
    export.add_argument(
        "--out", type=Path, required=True, help="Destination directory"
    )


def handle(args) -> int:
    from validation.artifact_io import schema_store
    from validation.artifact_io.errors import ArtifactStructureError

    try:
        if args.schema_command == "list":
            return _list(args, schema_store)
        if args.schema_command == "export":
            return _export(args, schema_store)
    except schema_store.SchemaDependencyUnavailable as exc:
        print(f"[ERROR] {exc}")
        return EXIT_ERROR
    except ArtifactStructureError as exc:
        print(f"[ERROR] {exc}")
        return EXIT_ERROR
    return EXIT_USAGE


def _rows(schema_store) -> list[dict[str, str]]:
    return [
        {
            "name": name,
            "format_version": schema_store.schema_version(name),
            "filename": schema_store.schema_filename(name),
        }
        for name in schema_store.schema_names()
    ]


def _list(args, schema_store) -> int:
    rows = _rows(schema_store)
    if args.format == "json":
        payload = {
            "schema_dialect": schema_store.SCHEMA_DIALECT,
            "jsonschema_required": schema_store.REQUIRED_JSONSCHEMA_VERSION,
            "jsonschema_installed": schema_store.jsonschema_version(),
            "schema_count": len(rows),
            "schemas": rows,
        }
        print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))
        return EXIT_OK

    width = max(len(row["name"]) for row in rows)
    print(f"Draft: {schema_store.SCHEMA_DIALECT}")
    print(f"Schemas: {len(rows)}")
    print()
    for row in rows:
        print(f"  {row['name']:<{width}}  {row['format_version']:<8}  {row['filename']}")
    return EXIT_OK


def _export(args, schema_store) -> int:
    written = schema_store.export_schemas(args.out)
    print(f"Wrote {len(written)} schema(s) to {args.out}")
    for filename in written:
        print(f"  {filename}")
    return EXIT_OK
