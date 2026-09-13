# -*- coding: utf-8 -*-
"""Lightweight endpoint extraction for common server frameworks."""

import re
import io
import tokenize
from pathlib import Path

from modules.repository_files import iter_repository_files, read_text


SERVER_EXTENSIONS = {
    ".java", ".js", ".jsx", ".mjs", ".cjs", ".py",
    ".ts", ".tsx", ".mts", ".cts", ".go",
}

SPRING_MAPPING = re.compile(
    r"@(?P<kind>Get|Post|Put|Delete|Patch)Mapping\s*"
    r"(?:\(\s*(?:value|path)?\s*=?\s*)?"
    r"(?:\{\s*)?[\"'](?P<route>[^\"']*)[\"']",
    re.MULTILINE,
)
SPRING_REQUEST_MAPPING = re.compile(
    r"@RequestMapping\s*\((?P<args>[^)]*)\)", re.MULTILINE | re.DOTALL
)
SPRING_ROUTE = re.compile(
    r"(?:value|path)\s*=\s*(?:\{\s*)?[\"'](?P<route>[^\"']*)[\"']"
    r"|^[ \t]*[\"'](?P<bare>[^\"']*)[\"']",
    re.MULTILINE,
)
SPRING_METHOD = re.compile(r"RequestMethod\.(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)")
SPRING_CLASS_PREFIX = re.compile(
    r"@RequestMapping\s*\((?P<args>[^)]*)\)\s*"
    r"(?:public\s+)?(?:abstract\s+)?class\s+\w+",
    re.MULTILINE | re.DOTALL,
)

EXPRESS = re.compile(
    r"\b(?:app|router|server|fastify)\s*\.\s*"
    r"(get|post|put|delete|patch|options|head|all)"
    r"\s*\(\s*[\"'`]([^\"'`]+)[\"'`]",
    re.MULTILINE,
)
EXPRESS_CHAIN = re.compile(
    r"\b(?:app|router|server|fastify)\s*\.\s*route\s*"
    r"\(\s*[\"'`]([^\"'`]+)[\"'`]\s*\)"
    r"(?P<chain>(?:\s*\.\s*(?:get|post|put|delete|patch|options|head|all)"
    r"\s*\([^;]*?\))+)",
    re.MULTILINE | re.DOTALL,
)
EXPRESS_CHAIN_METHOD = re.compile(
    r"\.\s*(get|post|put|delete|patch|options|head|all)\s*\("
)
FASTIFY_ROUTE = re.compile(
    r"\bfastify\s*\.\s*route\s*\(\s*\{(?P<body>.*?)\}\s*\)",
    re.MULTILINE | re.DOTALL,
)
FASTIFY_METHOD = re.compile(
    r"\bmethod\s*:\s*[\"'`](GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)[\"'`]",
    re.I,
)
FASTIFY_URL = re.compile(r"\b(?:url|path)\s*:\s*[\"'`]([^\"'`]+)[\"'`]")
PYTHON_ROUTE = re.compile(
    r"@\s*(?:app|router|blueprint|bp)\s*\.\s*"
    r"(route|get|post|put|delete|patch|options|head)"
    r"\s*\(\s*[\"']([^\"']+)[\"'](?P<args>[^)]*)\)",
    re.MULTILINE | re.DOTALL,
)
FLASK_METHODS = re.compile(r"methods\s*=\s*[\[(]([^\])]+)")
QUOTED_METHOD = re.compile(r"[\"'](GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)[\"']", re.I)
DJANGO = re.compile(r"\b(?:path|re_path)\s*\(\s*r?[\"']([^\"']+)[\"']", re.MULTILINE)
GO_ROUTE = re.compile(
    r"\.\s*(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD|"
    r"Get|Post|Put|Delete|Patch|Options|Head|Any|Handle|HandleFunc)"
    r"\s*\(\s*[\"'`]([^\"'`]+)[\"'`]",
    re.MULTILINE,
)
GO_HTTP_HANDLE = re.compile(
    r"\bhttp\s*\.\s*HandleFunc\s*\(\s*[\"'`]([^\"'`]+)[\"'`]",
    re.MULTILINE,
)
JAX_RS_PATH = re.compile(r"@Path\s*\(\s*[\"']([^\"']*)[\"']\s*\)")
JAX_RS_METHOD = re.compile(r"@(GET|POST|PUT|DELETE|PATCH|OPTIONS|HEAD)\b")
JAX_RS_METHOD_BLOCK = re.compile(
    r"(?P<annotations>(?:\s*@\w+(?:\([^)]*\))?\s*)+)"
    r"(?:public|protected|private)?\s+[\w$<>,.?\[\]]+\s+\w+\s*\(",
    re.MULTILINE,
)


def _strip_c_style_comments(content):
    """Blank // and /* */ comments while preserving route string literals."""
    chars = list(content)
    index = 0
    quote = None
    while index < len(chars):
        char = chars[index]
        following = chars[index + 1] if index + 1 < len(chars) else ""
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {'"', "'", "`"}:
            quote = char
            index += 1
            continue
        if char == "/" and following == "/":
            end = content.find("\n", index + 2)
            end = len(chars) if end == -1 else end
            for position in range(index, end):
                chars[position] = " "
            index = end
            continue
        if char == "/" and following == "*":
            end = content.find("*/", index + 2)
            end = len(chars) - 2 if end == -1 else end
            for position in range(index, min(end + 2, len(chars))):
                if chars[position] != "\n":
                    chars[position] = " "
            index = end + 2
            continue
        index += 1
    return "".join(chars)


def _strip_python_comments(content):
    try:
        tokens = tokenize.generate_tokens(io.StringIO(content).readline)
        return tokenize.untokenize(
            token
            for token in tokens
            if token.type != tokenize.COMMENT
        )
    except (IndentationError, tokenize.TokenError):
        return content


def _add(results, seen, method, route, path, repo_path):
    method = method.upper()
    route = route.strip() or "/"
    relative_path = path.relative_to(repo_path).as_posix()
    key = (method, route, relative_path)
    if key not in seen:
        seen.add(key)
        results.append({"method": method, "route": route, "file": relative_path})


def _join_routes(prefix, route):
    if not prefix or prefix == "/":
        return route or "/"
    if not route or route == "/":
        return "/" + prefix.strip("/")
    return "/" + "/".join((prefix.strip("/"), route.strip("/")))


def _extract_spring(content, path, repo_path, results, seen):
    class_match = SPRING_CLASS_PREFIX.search(content)
    class_prefix = ""
    class_mapping_start = None
    if class_match:
        route_match = SPRING_ROUTE.search(class_match.group("args"))
        if route_match:
            class_prefix = route_match.group("route") or route_match.group("bare") or ""
        class_mapping_start = class_match.start()

    for match in SPRING_MAPPING.finditer(content):
        _add(
            results,
            seen,
            match.group("kind"),
            _join_routes(class_prefix, match.group("route")),
            path,
            repo_path,
        )
    for match in SPRING_REQUEST_MAPPING.finditer(content):
        if match.start() == class_mapping_start:
            continue
        args = match.group("args")
        route_match = SPRING_ROUTE.search(args)
        route = (
            (route_match.group("route") or route_match.group("bare"))
            if route_match
            else "/"
        )
        methods = SPRING_METHOD.findall(args)
        for method in methods or ["ANY"]:
            _add(results, seen, method, _join_routes(class_prefix, route), path, repo_path)

    declarations = list(re.finditer(r"\b(?:class|interface)\s+\w+", content))
    for block in JAX_RS_METHOD_BLOCK.finditer(content):
        annotations = block.group("annotations")
        method_match = JAX_RS_METHOD.search(annotations)
        if not method_match:
            continue
        class_path = ""
        declaration_index = None
        for index, declaration in enumerate(declarations):
            if declaration.start() < block.start():
                declaration_index = index
            else:
                break
        if declaration_index is not None:
            declaration = declarations[declaration_index]
            previous_start = (
                declarations[declaration_index - 1].end()
                if declaration_index > 0
                else 0
            )
            paths = JAX_RS_PATH.findall(
                content[previous_start : declaration.start()]
            )
            if paths:
                class_path = paths[-1]
        route_match = JAX_RS_PATH.search(annotations)
        route = route_match.group(1) if route_match else "/"
        _add(
            results,
            seen,
            method_match.group(1),
            _join_routes(class_path, route),
            path,
            repo_path,
        )


def _extract_python(content, path, repo_path, results, seen):
    for match in PYTHON_ROUTE.finditer(content):
        decorator = match.group(1).lower()
        route = match.group(2)
        if decorator == "route":
            methods_match = FLASK_METHODS.search(match.group("args"))
            methods = (
                QUOTED_METHOD.findall(methods_match.group(1))
                if methods_match
                else ["GET"]
            )
        else:
            methods = [decorator]
        for method in methods:
            _add(results, seen, method, route, path, repo_path)

    if path.name.lower() in {"urls.py", "url.py"}:
        for route in DJANGO.findall(content):
            _add(results, seen, "ANY", route, path, repo_path)


def extract_endpoints(repo_path, language_hint=None, inventory=None):
    """Return deduplicated endpoints with their source file."""
    del language_hint  # File extensions are more reliable in polyglot repositories.
    repo_path = Path(repo_path)
    if not repo_path.is_dir():
        raise FileNotFoundError(f"Repository path does not exist: {repo_path}")

    results = []
    seen = set()
    for path in iter_repository_files(
        repo_path, extensions=SERVER_EXTENSIONS, include_tests=False, inventory=inventory
    ):
        content = read_text(path, inventory=inventory)
        if content is None:
            continue
        ext = path.suffix.lower()
        if ext == ".java":
            content = _strip_c_style_comments(content)
            _extract_spring(content, path, repo_path, results, seen)
        elif ext in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts"}:
            content = _strip_c_style_comments(content)
            for method, route in EXPRESS.findall(content):
                _add(results, seen, method, route, path, repo_path)
            for match in EXPRESS_CHAIN.finditer(content):
                route = match.group(1)
                for method in EXPRESS_CHAIN_METHOD.findall(match.group("chain")):
                    _add(results, seen, method, route, path, repo_path)
            for match in FASTIFY_ROUTE.finditer(content):
                route_match = FASTIFY_URL.search(match.group("body"))
                if not route_match:
                    continue
                methods = FASTIFY_METHOD.findall(match.group("body")) or ["ANY"]
                for method in methods:
                    _add(results, seen, method, route_match.group(1), path, repo_path)
        elif ext == ".py":
            content = _strip_python_comments(content)
            _extract_python(content, path, repo_path, results, seen)
        elif ext == ".go":
            content = _strip_c_style_comments(content)
            for method, route in GO_ROUTE.findall(content):
                normalized = (
                    "ANY" if method in {"Handle", "HandleFunc", "Any"}
                    else method.upper()
                )
                _add(results, seen, normalized, route, path, repo_path)
            for route in GO_HTTP_HANDLE.findall(content):
                _add(results, seen, "ANY", route, path, repo_path)

    return sorted(results, key=lambda ep: (ep["route"], ep["method"], ep["file"]))
