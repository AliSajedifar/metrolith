#!/usr/bin/env python3
"""Stratified, independently counted entity samples for the eight pilot repositories."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from collections import Counter
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from typing import Any, Iterable

if __package__:
    from .independent_inventory_check import BatchReader, cache_for, tree_entries
else:
    from independent_inventory_check import BatchReader, cache_for, tree_entries

# Imported only for the observed/actual side of the comparison. Expected counts
# below are produced by separate traversal code in this file.
from modules.core_metrics import ParserRegistry, _analyze_file


def iter_nodes(node: Any) -> Iterable[Any]:
    yield node
    for child in node.named_children:
        yield from iter_nodes(child)


def has_ancestor(node: Any, kinds: set[str]) -> bool:
    current = node.parent
    while current is not None:
        if current.type in kinds:
            return True
        current = current.parent
    return False


def at_module_scope(node: Any) -> bool:
    current = node.parent
    blockers = {
        "function_declaration", "function_expression", "arrow_function",
        "generator_function", "generator_function_declaration", "method_definition",
        "class_body", "object",
    }
    while current is not None:
        if current.type in blockers:
            return False
        if current.type in {"program", "source_file"}:
            return True
        current = current.parent
    return False


def node_text(node: Any, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def expected_python(source: bytes) -> tuple[int, int, dict[str, int], bool]:
    tree = ast.parse(source.decode("utf-8", errors="replace"))
    secondary = Counter()

    def visit_body(body: list[ast.stmt], scope: str) -> tuple[int, int]:
        classes = methods = 0
        for item in body:
            if isinstance(item, ast.ClassDef):
                classes += 1
                class_classes, class_methods = visit_class(item)
                classes += class_classes
                methods += class_methods
            elif isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if scope == "module":
                    methods += 1
                    secondary["module_functions"] += 1
                elif scope == "class":
                    if item.name in {"__init__", "__new__"}:
                        secondary["constructors"] += 1
                    else:
                        methods += 1
                        secondary["class_methods"] += 1
                else:
                    secondary["nested_functions"] += 1
                for nested in ast.walk(item):
                    if isinstance(nested, ast.Lambda):
                        secondary["lambdas"] += 1
                    elif nested is not item and isinstance(nested, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        secondary["nested_functions"] += 1
            elif scope == "module":
                # Classes/functions guarded by module-level control flow remain module definitions.
                for child in ast.iter_child_nodes(item):
                    if isinstance(child, ast.stmt):
                        c, m = visit_body([child], scope)
                        classes += c
                        methods += m
        return classes, methods

    def visit_class(node: ast.ClassDef) -> tuple[int, int]:
        nested_classes = methods = 0
        for child in node.body:
            if isinstance(child, ast.ClassDef):
                nested_classes += 1
                c, m = visit_class(child)
                nested_classes += c
                methods += m
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if child.name in {"__init__", "__new__"}:
                    secondary["constructors"] += 1
                else:
                    methods += 1
                    secondary["class_methods"] += 1
                for nested in ast.walk(child):
                    if isinstance(nested, ast.Lambda):
                        secondary["lambdas"] += 1
                    elif nested is not child and isinstance(nested, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        secondary["nested_functions"] += 1
        return nested_classes, methods

    classes, methods = visit_body(tree.body, "module")
    return classes, methods, dict(secondary), False


def expected_tree(language: str, extension: str, source: bytes) -> tuple[int, int, dict[str, int], bool]:
    root = ParserRegistry().get(language, extension).parse(source).root_node
    secondary = Counter()
    classes = methods = 0
    if language == "Java":
        for node in iter_nodes(root):
            if node.type in {"class_declaration", "record_declaration"}:
                classes += 1
            elif node.type == "method_declaration":
                if node.child_by_field_name("body") is None:
                    secondary["signature_only_methods"] += 1
                else:
                    methods += 1
            elif node.type == "constructor_declaration":
                secondary["constructors"] += 1
            elif node.type in {"interface_declaration", "enum_declaration", "annotation_type_declaration"}:
                secondary[node.type] += 1
            elif node.type == "object_creation_expression" and any(child.type == "class_body" for child in node.named_children):
                secondary["anonymous_classes"] += 1
    elif language == "Go":
        for node in iter_nodes(root):
            if node.type == "type_spec":
                declared = node.child_by_field_name("type")
                if declared is not None and declared.type == "struct_type":
                    classes += 1
                elif declared is not None and declared.type == "interface_type":
                    secondary["interfaces"] += 1
            elif node.type in {"function_declaration", "method_declaration"}:
                methods += 1
            elif node.type == "func_literal":
                secondary["anonymous_functions"] += 1
    else:
        callable_kinds = {
            "function_declaration", "function_expression", "arrow_function",
            "generator_function", "generator_function_declaration", "method_definition",
        }
        for node in iter_nodes(root):
            if node.type in {"class_declaration", "abstract_class_declaration"}:
                classes += 1
            elif node.type == "class":
                secondary["anonymous_classes"] += 1
            elif node.type in {"interface_declaration", "enum_declaration", "type_alias_declaration"}:
                secondary[node.type] += 1
            elif node.type in {"function_signature", "method_signature", "abstract_method_signature"}:
                secondary["signature_only_methods"] += 1
            elif node.type in {"function_declaration", "generator_function_declaration"}:
                if has_ancestor(node, callable_kinds):
                    secondary["nested_functions"] += 1
                elif at_module_scope(node):
                    methods += 1
            elif node.type in {"function_expression", "arrow_function", "generator_function"}:
                parent = node.parent
                stable_variable = bool(
                    parent is not None and parent.type == "variable_declarator"
                    and parent.child_by_field_name("value") == node
                    and parent.child_by_field_name("name") is not None
                    and at_module_scope(parent)
                )
                if stable_variable:
                    methods += 1
                elif has_ancestor(node, callable_kinds):
                    secondary["nested_functions"] += 1
                else:
                    secondary["anonymous_functions"] += 1
            elif node.type == "method_definition":
                name = node.child_by_field_name("name")
                body = node.child_by_field_name("body")
                if name is not None and node_text(name, source) == "constructor":
                    secondary["constructors"] += 1
                elif body is None:
                    secondary["signature_only_methods"] += 1
                elif node.parent is not None and node.parent.type == "class_body":
                    methods += 1
                elif node.parent is not None and node.parent.type == "object":
                    obj = node.parent
                    declaration = obj.parent
                    if declaration is not None and declaration.type == "variable_declarator" and at_module_scope(declaration):
                        methods += 1
    return classes, methods, dict(secondary), bool(root.has_error)


def independent_expected(language: str, extension: str, source: bytes):
    if language == "Python":
        return expected_python(source)
    return expected_tree(language, extension, source)


def choose_samples(records: list[dict[str, Any]], contents: dict[str, bytes], primary: str) -> list[tuple[str, str]]:
    included = [item for item in records if item["included_in_metrics"] and item["detected_language"] == primary]
    excluded = [item for item in records if item["is_source"] and not item["included_in_metrics"] and item["detected_language"] == primary]
    chosen: list[tuple[str, str]] = []

    def add(record, reason):
        if record and all(record["relative_path"] != path for path, _ in chosen):
            chosen.append((record["relative_path"], reason))

    nonempty = [item for item in included if (item.get("size_bytes") or 0) > 0]
    add(min(nonempty, key=lambda item: item["size_bytes"], default=None), "small included file")
    add(max(included, key=lambda item: item.get("size_bytes") or 0, default=None), "large included file")
    framework = re.compile(rb"(?i)(controller|router|route|endpoint|fastapi|flask|django|spring|react|component|handler)")
    constructs = re.compile(rb"(?i)(class\s+|struct\s*\{|interface\s+|constructor\s*\(|__init__|=>|func\s+\(|lambda)")
    add(next((item for item in included if framework.search(contents.get(item["relative_path"], b""))), None), "framework-heavy construct")
    add(next((item for item in included if constructs.search(contents.get(item["relative_path"], b""))), None), "entity/callable edge constructs")
    add(next((item for item in included if item.get("parse_status") in {"partial", "failed"}), None), "parser-error file")
    if len(included) >= 3:
        for fraction, label in ((0.25, "lower-size quartile"), (0.5, "median-size file"), (0.75, "upper-size quartile")):
            ordered = sorted(included, key=lambda item: (item.get("size_bytes") or 0, item["relative_path"]))
            add(ordered[min(int((len(ordered) - 1) * fraction), len(ordered) - 1)], label)
    add(next((item for item in excluded if item["exclusion_reason"] == "test"), None), "excluded test-looking file")
    add(next((item for item in excluded if item["exclusion_reason"] in {"generated", "build_output"}), None), "excluded generated/build file")
    add(next((item for item in excluded if item["exclusion_reason"] in {"vendor", "dependency"}), None), "excluded vendor/dependency file")
    return chosen[:8]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("cache_root", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    run_dir = args.run_dir.resolve()
    analysis = json.loads((run_dir / "analysis.json").read_text(encoding="utf-8"))
    comparisons = []
    for result in analysis:
        url = result["repository_url"]
        sha = result["acquisition"]["analyzed_commit_sha"]
        primary = result["metrics"]["primary_language_name"]
        slug = f"{result['repository_owner']}__{result['repository_name']}"
        inventory = json.loads((run_dir / "file_inventory" / f"{slug}.json").read_text(encoding="utf-8"))
        records = inventory["files"]
        cache = cache_for(args.cache_root.resolve(), url)
        objects = {entry["path"]: entry for entry in tree_entries(cache, sha)}
        relevant = [item for item in records if item.get("detected_language") == primary]
        batch = BatchReader(cache)
        contents = {}
        try:
            for record in relevant:
                entry = objects.get(record["relative_path"])
                if entry and entry["kind"] == "blob" and (entry["size"] or 0) <= 5 * 1024 * 1024:
                    contents[record["relative_path"]] = batch.read(entry["object_id"])
        finally:
            batch.close()
        by_path = {item["relative_path"]: item for item in records}
        samples = choose_samples(records, contents, primary)
        # Include a secondary-language parser failure even when expected_language fixes primary.
        parser_error = next((item for item in records if item.get("parse_status") in {"partial", "failed"}), None)
        if parser_error and all(parser_error["relative_path"] != path for path, _ in samples):
            samples.append((parser_error["relative_path"], "secondary-language parser-error file"))
            entry = objects[parser_error["relative_path"]]
            reader = BatchReader(cache)
            try:
                contents[parser_error["relative_path"]] = reader.read(entry["object_id"])
            finally:
                reader.close()
        for path, selection_reason in samples:
            record = by_path[path]
            source = contents.get(path, b"")
            if not record["included_in_metrics"]:
                expected_classes = expected_methods = actual_classes = actual_methods = 0
                expected_secondary = {}
                expected_has_error = False
                actual_status = "not_applicable_excluded"
                actual_error = None
            else:
                try:
                    expected_classes, expected_methods, expected_secondary, expected_has_error = independent_expected(
                        record["detected_language"], record["extension"], source
                    )
                except (SyntaxError, ValueError) as exc:
                    expected_classes = expected_methods = None
                    expected_secondary = {}
                    expected_has_error = True
                observed = _analyze_file(
                    SimpleNamespace(detected_language=record["detected_language"], extension=record["extension"]),
                    source,
                    ParserRegistry(),
                )
                if observed.entities is None:
                    actual_classes = actual_methods = None
                else:
                    actual_classes = observed.entities["classes"] + observed.entities["records"] + observed.entities["structs"]
                    actual_methods = observed.entities["module_functions"] + observed.entities["class_methods"] + observed.entities["receiver_methods"]
                actual_status = observed.status
                actual_error = observed.error
            agreement = expected_classes == actual_classes and expected_methods == actual_methods
            comparisons.append({
                "repository_url": url,
                "commit_sha": sha,
                "sample_path": path,
                "selection_reason": selection_reason,
                "language": record["detected_language"],
                "included_in_metrics": record["included_in_metrics"],
                "exclusion_reason": record["exclusion_reason"],
                "content_sha256": hashlib.sha256(source).hexdigest() if source else record.get("content_hash"),
                "expected_classes_structs": expected_classes,
                "actual_classes_structs": actual_classes,
                "expected_methods_functions": expected_methods,
                "actual_methods_functions": actual_methods,
                "expected_secondary": expected_secondary,
                "expected_tree_has_error": expected_has_error,
                "actual_status": actual_status,
                "actual_error": actual_error,
                "agreement": agreement,
                "disagreement_category": None if agreement else ("parser_limitation" if expected_has_error or actual_status != "complete" else "entity_extraction_disagreement"),
            })
    payload = {
        "method": "stratified independent AST traversal; production _analyze_file used only for observed counts",
        "sample_count": len(comparisons),
        "agreement_count": sum(item["agreement"] for item in comparisons),
        "disagreement_count": sum(not item["agreement"] for item in comparisons),
        "samples": comparisons,
    }
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
