"""Reference drivers for ArchLens Cognitive Complexity differential validation.

Four references, five languages, and two of them need an identity enumerator
beside the metric run. Everything here is a *reference* driver: no primary
independent adapter exists for Cognitive Complexity, so nothing produced here is
evidence about an ArchLens implementation on its own.

Three harness rules carried over from the Complexity Contract 1.0.0 drivers,
because all three were violated once already and every violation was silent:

* **an empty scope is refused, never reported** -- a zero-observation result and
  a broken input must never look alike;
* **paths are passed by listing file, never on a command line** -- a real
  subject exceeds the Windows command-line limit and fails as an opaque
  ``WinError 206``;
* **existence is not freshness** -- a tool is trusted because its recorded
  provenance matches, not because a file is on disk.

The Go toolchain escalation, resolved
=====================================

G0 recorded that ``go install gocognit@v1.2.1`` **silently escalated** from the
pinned Go 1.23.12 to a freshly downloaded go1.25.12, because the module requires
Go >= 1.24. A reference environment that believes it is pinned to 1.23.12 is
not, and G0 left the decision open.

**Decision: two explicitly pinned Go toolchains, and a refusal in place of the
escalation.**

===========================  ==========  ==================================
Role                         Toolchain   Why
===========================  ==========  ==================================
Complexity Contract 1.0.0    go1.23.12   unchanged, so every C4/CX result
Go adapter (measurement)                 stays reproducible against the
                                         toolchain that produced it
gocognit build only          go1.25.12   gocognit 1.2.1 requires >= 1.24
                                         and CANNOT be built by 1.23.12
===========================  ==========  ==================================

Rejecting the alternatives, briefly: downgrading to an older gocognit changes
the reference implementation to validate a build detail, and pretending 1.23.12
built it is false. Two pinned toolchains is the only option that keeps both
claims true.

**The escalation is now explicit and offline.** :func:`go_build_environment`
sets ``GOTOOLCHAIN`` to the exact pinned version -- never ``auto`` -- and
``GOPROXY=off``, and :func:`refuse_silent_escalation` raises when the
environment would permit a download.

**And the pin is verified from the ARTIFACT, not the environment.**
:func:`verify_gocognit_provenance` reads ``go version -m gocognit.exe``, which
carries the toolchain that actually compiled the binary and the exact module
versions linked into it. An environment variable states an intention; the
embedded build info states what happened. Only the second is evidence, and it is
the one checked before any measurement runs.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from validation.differential.cognitive_zero_suppression import SuppressionProbe

REFERENCE_ROOT = Path(
    os.environ.get("ARCHLENS_DIFFVAL_REFS", ".metrolith-reference")
)

PYREF = REFERENCE_ROOT / "pyref/Scripts/python.exe"
NODE = REFERENCE_ROOT / "node-v22.20.0-win-x64/node.exe"
NODE_MODULES_G0 = REFERENCE_ROOT / "nodepkgs-g0/node_modules"
GO = REFERENCE_ROOT / "go/bin/go.exe"
GOCOGNIT = REFERENCE_ROOT / "gobin/gocognit.exe"
PMD = REFERENCE_ROOT / "pmd/pmd-bin-7.7.0/bin/pmd.bat"

RESOURCES = Path(__file__).resolve().parent / "cognitive"
SONARJS_DRIVER = RESOURCES / "sonarjs_cognitive.js"
PYTHON_DRIVER = RESOURCES / "python_cognitive.py"
PMD_METRIC_RULESET = RESOURCES / "pmd_cognitive.xml"
PMD_ENUMERATE_RULESET = RESOURCES / "pmd_enumerate.xml"

_TIMEOUT_SECONDS = 1800

# ---------------------------------------------------------------------------
# Go toolchain pins. Task 2.
# ---------------------------------------------------------------------------

#: The measurement toolchain, unchanged from the Complexity Contract 1.0.0
#: study. Moving it would invalidate every C4 Go result.
GO_MEASUREMENT_TOOLCHAIN = "1.23.12"

#: The toolchain that built gocognit, pinned EXACTLY rather than resolved.
GO_GOCOGNIT_BUILD_TOOLCHAIN = "1.25.12"

GOCOGNIT_VERSION = "1.2.1"

#: Linked into the binary. Recorded so a rebuild that picks up a different
#: x/tools is visible rather than absorbed.
GOCOGNIT_DEPENDENCIES = {"golang.org/x/tools": "0.42.0"}

GO_TOOLCHAIN_PINS: dict[str, str] = {
    "go_measurement_toolchain": GO_MEASUREMENT_TOOLCHAIN,
    "go_gocognit_build_toolchain": GO_GOCOGNIT_BUILD_TOOLCHAIN,
    "gocognit": GOCOGNIT_VERSION,
    **GOCOGNIT_DEPENDENCIES,
}

#: `GOTOOLCHAIN` values that permit the toolchain to change under the harness.
#: `auto` downloads a newer toolchain when a module asks for one; `path` picks
#: whatever is on PATH. Both are exactly the silent escalation G0 recorded.
_ESCALATING_GOTOOLCHAIN = ("auto", "path", "", None)


class ToolchainRefused(RuntimeError):
    """The Go toolchain is not explicitly pinned, or is not the pinned one.

    Typed and raised, never warned: a run that escalated its own toolchain
    produces numbers that look exactly like a pinned run's.
    """


class ReferenceUnavailable(RuntimeError):
    """A reference tool is not provisioned. A capability statement about a run."""


class ScopeRefused(RuntimeError):
    """A comparison resolved to no files. Typed, so it cannot become a zero."""


def refuse_silent_escalation(environment: dict[str, str]) -> dict[str, str]:
    """Refuse any environment in which the Go toolchain could change itself.

    Checked on the environment the harness is about to USE, so a caller cannot
    hand-roll a build that bypasses the pin.
    """
    requested = environment.get("GOTOOLCHAIN")
    if requested in _ESCALATING_GOTOOLCHAIN:
        raise ToolchainRefused(
            f"GOTOOLCHAIN={requested!r} permits Go to select or DOWNLOAD a "
            f"toolchain on its own. G0 recorded exactly this escalation: "
            f"gocognit {GOCOGNIT_VERSION} requires Go >= 1.24 and silently "
            f"pulled go1.25.12 over the pinned {GO_MEASUREMENT_TOOLCHAIN}. The "
            f"run is refused rather than allowed to produce numbers under an "
            f"unrecorded toolchain. Set GOTOOLCHAIN to an exact version."
        )
    if not str(requested).startswith("go1"):
        raise ToolchainRefused(
            f"GOTOOLCHAIN={requested!r} is not an exact toolchain version. "
            f"Expected a pinned value such as 'go{GO_GOCOGNIT_BUILD_TOOLCHAIN}'."
        )
    if environment.get("GOPROXY") != "off":
        raise ToolchainRefused(
            f"GOPROXY={environment.get('GOPROXY')!r} permits a module or "
            f"toolchain fetch. The reference environment is provisioned ahead "
            f"of time and every build must be offline, so a fetch means the "
            f"pin was wrong rather than that the network should be used."
        )
    return environment


def go_build_environment(*, toolchain: str = GO_GOCOGNIT_BUILD_TOOLCHAIN) -> dict[str, str]:
    """The only environment in which a gocognit build may happen.

    Explicit toolchain, no proxy, and the module cache kept inside the reference
    root so a build cannot quietly populate the user's global GOPATH.
    """
    environment = {
        **os.environ,
        "GOTOOLCHAIN": f"go{toolchain}",
        "GOPROXY": "off",
        "GOFLAGS": "-mod=mod",
        "GOPATH": str(REFERENCE_ROOT / "gopath"),
        "GOMODCACHE": str(REFERENCE_ROOT / "gopath" / "pkg" / "mod"),
    }
    return refuse_silent_escalation(environment)


@dataclass(frozen=True)
class GocognitProvenance:
    """What actually built the gocognit binary, read from the binary itself."""

    binary: Path
    build_toolchain: str
    module_version: str
    dependencies: dict[str, str]
    matches_pins: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "binary": str(self.binary),
            "build_toolchain": self.build_toolchain,
            "pinned_build_toolchain": f"go{GO_GOCOGNIT_BUILD_TOOLCHAIN}",
            "module_version": self.module_version,
            "pinned_module_version": f"v{GOCOGNIT_VERSION}",
            "dependencies": dict(sorted(self.dependencies.items())),
            "matches_pins": self.matches_pins,
            "detail": self.detail,
            "measurement_toolchain_unchanged": GO_MEASUREMENT_TOOLCHAIN,
        }


def verify_gocognit_provenance(
    binary: Path = GOCOGNIT, *, go: Path = GO
) -> GocognitProvenance:
    """Read the toolchain and module versions embedded in the built binary.

    ``go version -m`` reports what compiled the artifact, which is the only
    statement that cannot be wrong about what happened. An environment variable
    records an intention; this records the outcome.
    """
    if not Path(binary).is_file():
        raise ReferenceUnavailable(f"gocognit is not provisioned at {binary}")
    if not Path(go).is_file():
        raise ReferenceUnavailable(f"the Go toolchain is not provisioned at {go}")

    completed = subprocess.run(
        [str(go), "version", "-m", str(binary)],
        capture_output=True, text=True, timeout=_TIMEOUT_SECONDS,
    )
    if completed.returncode != 0:
        raise ToolchainRefused(
            f"could not read build provenance from {binary}: "
            f"{completed.stderr.strip()[:400]}"
        )

    text = completed.stdout
    toolchain_match = re.search(r":\s*(go\d+\.\d+(?:\.\d+)?)", text)
    module_match = re.search(r"^\s*mod\s+\S+\s+(\S+)", text, re.MULTILINE)
    dependencies = {
        name: version
        for name, version in re.findall(r"^\s*dep\s+(\S+)\s+(\S+)", text, re.MULTILINE)
    }

    build_toolchain = toolchain_match.group(1) if toolchain_match else "unknown"
    module_version = module_match.group(1) if module_match else "unknown"

    problems: list[str] = []
    if build_toolchain != f"go{GO_GOCOGNIT_BUILD_TOOLCHAIN}":
        problems.append(
            f"built by {build_toolchain}, pinned go{GO_GOCOGNIT_BUILD_TOOLCHAIN}"
        )
    if module_version != f"v{GOCOGNIT_VERSION}":
        problems.append(f"module {module_version}, pinned v{GOCOGNIT_VERSION}")
    for name, expected in GOCOGNIT_DEPENDENCIES.items():
        actual = dependencies.get(name)
        if actual != f"v{expected}":
            problems.append(f"{name} {actual}, pinned v{expected}")

    return GocognitProvenance(
        binary=Path(binary),
        build_toolchain=build_toolchain,
        module_version=module_version,
        dependencies=dependencies,
        matches_pins=not problems,
        detail=(
            "embedded build info matches every pin"
            if not problems
            else "; ".join(problems)
        ),
    )


def require_pinned_gocognit(binary: Path = GOCOGNIT, *, go: Path = GO) -> GocognitProvenance:
    """Provenance, or a refusal. Called before any Go measurement."""
    provenance = verify_gocognit_provenance(binary, go=go)
    if not provenance.matches_pins:
        raise ToolchainRefused(
            f"gocognit provenance does not match the study pins: "
            f"{provenance.detail}. A reference built by an unrecorded toolchain "
            f"produces numbers indistinguishable from a pinned one, so the run "
            f"is refused."
        )
    return provenance


# ---------------------------------------------------------------------------
# Shared plumbing
# ---------------------------------------------------------------------------

SOURCE_EXTENSIONS = {
    "Go": {".go"},
    "Java": {".java"},
    "JavaScript": {".js", ".jsx", ".mjs", ".cjs"},
    "TypeScript": {".ts", ".tsx", ".mts", ".cts"},
    "Python": {".py"},
}


@dataclass(frozen=True)
class ReferenceRun:
    """One reference invocation's normalized output."""

    language: str
    reference: str
    reference_version: str
    #: Rows carrying a value. Keys match `callable_matching.key_from_row`.
    rows: list[dict[str, Any]]
    #: Rows proving a callable EXISTS, with no value attached. Empty when the
    #: reference reports zeros itself and needs no enumerator.
    enumerated: list[dict[str, Any]]
    unreadable_files: list[dict[str, str]]
    invocation: str
    #: Anything the tool emitted that is NOT the measurement -- counted, never
    #: dropped silently, so a linter-configuration change is visible.
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "reference": self.reference,
            "reference_version": self.reference_version,
            "row_count": len(self.rows),
            "enumerated_count": len(self.enumerated),
            "unreadable_files": self.unreadable_files[:20],
            "unreadable_file_count": len(self.unreadable_files),
            "invocation": self.invocation,
            "non_measurement_diagnostics": dict(self.diagnostics),
        }


def resolve_scope(language: str, root: Path, files: Sequence[str]) -> tuple[str, ...]:
    """Refuse an empty or wrong-language scope before any tool is invoked."""
    if not Path(root).is_dir():
        raise ScopeRefused(f"{language}: subject root is not a directory: {root}")
    if not files:
        raise ScopeRefused(
            f"{language}: resolved to ZERO files. This is an input or harness "
            f"error, not an empty comparison. Check that the path is a SOURCE "
            f"CHECKOUT and not an ArchLens workspace."
        )
    expected = SOURCE_EXTENSIONS.get(language, set())
    unexpected = sorted(
        {Path(item).suffix.lower() for item in files} - expected
    )
    if unexpected:
        raise ScopeRefused(
            f"{language}: scope carries unexpected extensions {unexpected}"
        )
    return tuple(files)


def write_listing(paths: Sequence[str], destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "\n".join(str(item) for item in paths) + "\n", encoding="utf-8", newline="\n"
    )
    return destination


def _run(command: Sequence[str], **kwargs: Any) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(part) for part in command],
        capture_output=True, text=True, timeout=_TIMEOUT_SECONDS, **kwargs,
    )


# ---------------------------------------------------------------------------
# Go -- gocognit. Reports its own zeros under `-over -1`.
# ---------------------------------------------------------------------------


def run_gocognit(root: Path, files: Sequence[str]) -> ReferenceRun:
    """Measure Go, with zeros INCLUDED.

    ``-over -1`` is the whole point of this invocation and is not a detail:
    ``-over N`` means *strictly greater than N*, so the default ``-over 0``
    hides every genuine zero. G0 recorded gocognit as listing every function
    regardless of value, which is true only under a negative threshold, and
    G1-A's N3 concluded that gocognit cannot report a zero at all -- which is
    false. Go therefore needs no zero-suppression inference.
    """
    scope = resolve_scope("Go", root, files)
    provenance = require_pinned_gocognit()

    rows: list[dict[str, Any]] = []
    unreadable: list[dict[str, str]] = []
    invocation = f"{provenance.binary.name} -over -1 -json <file>"

    for relative in scope:
        absolute = Path(root) / relative
        completed = _run([GOCOGNIT, "-over", "-1", "-json", str(absolute)])
        if completed.returncode not in (0, 1) or not completed.stdout.strip():
            unreadable.append(
                {
                    "relative_path": str(relative).replace("\\", "/"),
                    "reason": (completed.stderr or "no output").strip()[:300],
                }
            )
            continue
        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            unreadable.append(
                {
                    "relative_path": str(relative).replace("\\", "/"),
                    "reason": f"unparsable gocognit JSON: {error}",
                }
            )
            continue
        for entry in payload or ():
            position = entry.get("Pos") or {}
            rows.append(
                {
                    "relative_path": str(relative).replace("\\", "/"),
                    "qualified_name": entry.get("FuncName"),
                    "start_line": position.get("Line"),
                    "end_line": position.get("Line"),
                    "value": entry.get("Complexity"),
                }
            )

    return ReferenceRun(
        language="Go",
        reference="gocognit",
        reference_version=GOCOGNIT_VERSION,
        rows=rows,
        enumerated=[],
        unreadable_files=unreadable,
        invocation=invocation,
    )


# ---------------------------------------------------------------------------
# Java -- PMD. Suppresses zero; needs the enumerator.
# ---------------------------------------------------------------------------

_PMD_COGNITIVE = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):\s+CognitiveComplexity:\s+The method "
    r"'(?P<name>[^']+)' has a cognitive complexity of (?P<value>\d+)"
)
_PMD_CYCLOMATIC = re.compile(
    r"^(?P<path>.+?):(?P<line>\d+):\s+CyclomaticComplexity:\s+The method "
    r"'(?P<name>[^']+)' has a cyclomatic complexity of (?P<value>\d+)"
)


def _pmd_rows(output: str, root: Path, pattern: re.Pattern[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in output.splitlines():
        found = pattern.match(line.strip())
        if not found:
            continue
        try:
            relative = Path(found["path"]).resolve().relative_to(Path(root).resolve())
            path = relative.as_posix()
        except ValueError:
            path = Path(found["path"]).name
        name = found["name"]
        # PMD names a method `name(ParamTypes)`. The parameter list is the only
        # thing that separates overloads, so it is kept as the signature
        # discriminator rather than discarded with the rest of the spelling.
        base, _, signature = name.partition("(")
        rows.append(
            {
                "relative_path": path,
                "qualified_name": base,
                "signature_discriminator": signature.rstrip(")") or None,
                "start_line": int(found["line"]),
                "end_line": int(found["line"]),
                "value": int(found["value"]),
            }
        )
    return rows


def run_pmd(root: Path, files: Sequence[str], work: Path) -> ReferenceRun:
    """Measure Java, and separately ENUMERATE Java.

    Two PMD invocations over the same file list. The second exists only to
    answer "did PMD's parser see a callable here?", because a callable scoring
    a genuine zero appears in no cognitive output at all and therefore cannot be
    matched from the metric run.
    """
    scope = resolve_scope("Java", root, files)
    if not PMD.is_file():
        raise ReferenceUnavailable(f"PMD is not provisioned at {PMD}")

    work.mkdir(parents=True, exist_ok=True)
    listing = write_listing(
        [str((Path(root) / item).resolve()) for item in scope], work / "pmd_files.txt"
    )

    runs: dict[str, str] = {}
    for mode, ruleset in (
        ("metric", PMD_METRIC_RULESET),
        ("enumerate", PMD_ENUMERATE_RULESET),
    ):
        completed = _run(
            [PMD, "check", "-R", str(ruleset), "-f", "text",
             "--file-list", str(listing), "--no-progress"]
        )
        # PMD exits 4 when it found violations, which is the normal case here:
        # every reported callable is a "violation" of a threshold set to its
        # minimum. Only a genuine error exit is a failure.
        if completed.returncode not in (0, 4):
            raise RuntimeError(
                f"PMD {mode} run failed (exit {completed.returncode}): "
                f"{(completed.stderr or completed.stdout).strip()[:600]}"
            )
        runs[mode] = completed.stdout

    return ReferenceRun(
        language="Java",
        reference="PMD CognitiveComplexity",
        reference_version="7.7.0",
        rows=_pmd_rows(runs["metric"], root, _PMD_COGNITIVE),
        enumerated=[
            {key: value for key, value in row.items() if key != "value"}
            for row in _pmd_rows(runs["enumerate"], root, _PMD_CYCLOMATIC)
        ],
        unreadable_files=[],
        invocation=(
            "pmd check -R pmd_cognitive.xml (reportLevel=1) + "
            "pmd check -R pmd_enumerate.xml (methodReportLevel=1)"
        ),
    )


# ---------------------------------------------------------------------------
# JavaScript / TypeScript -- SonarJS. Suppresses zero; needs the enumerator.
# ---------------------------------------------------------------------------


def run_sonarjs(language: str, root: Path, files: Sequence[str], work: Path) -> ReferenceRun:
    """Measure and enumerate JS or TS through the same parser, twice."""
    scope = resolve_scope(language, root, files)
    if not NODE.is_file():
        raise ReferenceUnavailable(f"Node is not provisioned at {NODE}")
    if not NODE_MODULES_G0.is_dir():
        raise ReferenceUnavailable(
            f"the G0 node package tree is not provisioned at {NODE_MODULES_G0}. "
            f"It is deliberately separate from `nodepkgs`, which the Complexity "
            f"Contract 1.0.0 study pinned and which must not be disturbed."
        )

    work.mkdir(parents=True, exist_ok=True)
    listing = write_listing(
        [str((Path(root) / item).resolve()) for item in scope],
        work / f"{language.lower()}_files.txt",
    )

    environment = {**os.environ, "NODE_PATH": str(NODE_MODULES_G0)}
    payloads: dict[str, dict[str, Any]] = {}
    for mode in ("metric", "enumerate"):
        completed = _run(
            [NODE, str(SONARJS_DRIVER), mode, str(listing), str(Path(root).resolve())],
            env=environment,
        )
        if completed.returncode != 0:
            raise RuntimeError(
                f"the SonarJS driver failed in {mode} mode "
                f"(exit {completed.returncode}): {completed.stderr.strip()[:600]}"
            )
        payloads[mode] = json.loads(completed.stdout)

    return ReferenceRun(
        language=language,
        reference="eslint-plugin-sonarjs S3776",
        reference_version="4.2.0",
        rows=[
            {
                "relative_path": row["relative_path"],
                "qualified_name": None,
                "start_line": row["start_line"],
                "end_line": row["end_line"],
                "value": row["value"],
            }
            for row in payloads["metric"]["rows"]
        ],
        enumerated=[
            {
                "relative_path": row["relative_path"],
                "qualified_name": None,
                "start_line": row["start_line"],
                "end_line": row["end_line"],
            }
            for row in payloads["enumerate"]["rows"]
        ],
        unreadable_files=[
            dict(item)
            for item in payloads["metric"]["unreadable_files"]
        ],
        invocation=(
            "node sonarjs_cognitive.js metric|enumerate -- "
            "sonarjs/cognitive-complexity threshold 0, ESLint core complexity threshold 0"
        ),
        diagnostics={
            mode: dict(payloads[mode].get("ignored_messages_by_rule") or {})
            for mode in ("metric", "enumerate")
        },
    )


# ---------------------------------------------------------------------------
# Python -- cognitive_complexity. Reports its own zeros.
# ---------------------------------------------------------------------------


def run_python_reference(root: Path, files: Sequence[str], work: Path) -> ReferenceRun:
    """Measure Python. Returns an int for every callable, zeros included."""
    scope = resolve_scope("Python", root, files)
    if not PYREF.is_file():
        raise ReferenceUnavailable(
            f"the Python reference interpreter is not provisioned at {PYREF}"
        )

    work.mkdir(parents=True, exist_ok=True)
    listing = write_listing(list(scope), work / "python_files.txt")
    completed = _run(
        [PYREF, str(PYTHON_DRIVER), str(listing), str(Path(root).resolve())]
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"the Python reference driver failed (exit {completed.returncode}): "
            f"{completed.stderr.strip()[:600]}"
        )
    payload = json.loads(completed.stdout)

    return ReferenceRun(
        language="Python",
        reference="cognitive_complexity",
        reference_version="1.3.0",
        rows=[dict(row) for row in payload["rows"]],
        enumerated=[],
        unreadable_files=[dict(item) for item in payload["unreadable_files"]],
        invocation="pyref python_cognitive.py <listing> <root>",
    )


# ---------------------------------------------------------------------------
# The live zero-suppression probe. Task 3, step 2.
# ---------------------------------------------------------------------------

PROBE_ROOT = (
    Path(__file__).resolve().parents[2]
    / "complexity_g2a_20260811"
    / "probes"
)

#: Probe file and the two callables in it, per language. The control lives in
#: the SAME file as the zero deliberately: a reported control proves the file
#: was read, so an absent zero is the tool's choice rather than a scope miss.
PROBE_CALLABLES: dict[str, tuple[str, str, str]] = {
    "Go": ("zeroprobe.go", "ZeroCallable", "NonZeroCallable"),
    "Java": ("ZeroProbe.java", "zeroCallable", "nonZeroCallable"),
    "JavaScript": ("zeroprobe.js", "zeroCallable", "nonZeroCallable"),
    "TypeScript": ("zeroprobe.ts", "zeroCallable", "nonZeroCallable"),
    "Python": ("zeroprobe.py", "zero_callable", "non_zero_callable"),
}


def probe_zero_suppression(language: str, work: Path) -> SuppressionProbe:
    """Measure, on THIS run, whether the reference can print a genuine zero.

    The returned probe still has to pass
    :func:`cognitive_zero_suppression.prove_suppression` before any absence may
    be interpreted. Measuring and licensing are separate calls on purpose:
    measurement alone must never authorize an inference.
    """
    filename, zero_name, control_name = PROBE_CALLABLES[language]
    from validation.differential.cognitive_zero_suppression import RECORDED_PROBES

    recorded = RECORDED_PROBES[language]
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)

    if language == "Go":
        run = run_gocognit(PROBE_ROOT, [filename])
    elif language == "Java":
        run = run_pmd(PROBE_ROOT, [filename], work)
    elif language in ("JavaScript", "TypeScript"):
        run = run_sonarjs(language, PROBE_ROOT, [filename], work)
    elif language == "Python":
        run = run_python_reference(PROBE_ROOT, [filename], work)
    else:
        raise KeyError(language)

    def value_for(name: str) -> int | None:
        for row in run.rows:
            qualified = row.get("qualified_name")
            if qualified and str(qualified).split(".")[-1] == name:
                return row.get("value")
        # SonarJS reports no name at all, so the probe falls back to the line
        # the callable is declared on. Both probe callables are unambiguous in
        # their own file, which is why the probe is two callables and not ten.
        line = _probe_declaration_line(filename, name)
        if line is None:
            return None
        for row in run.rows:
            start = row.get("start_line")
            if start is not None and abs(int(start) - line) <= 1:
                return row.get("value")
        return None

    return SuppressionProbe(
        language=language,
        reference=recorded.reference,
        reference_version=recorded.reference_version,
        zero_callable_value=value_for(zero_name),
        control_callable_value=value_for(control_name),
        mechanism=recorded.mechanism,
        invocation=run.invocation,
    )


def _probe_declaration_line(filename: str, callable_name: str) -> int | None:
    """The line a probe callable is declared on, found in the probe source."""
    source = (PROBE_ROOT / filename).read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(source, start=1):
        stripped = line.strip()
        if callable_name in stripped and (
            stripped.startswith(("func ", "def ", "export function ", "function "))
            or "(" in stripped and stripped.endswith("{")
        ):
            return index
    return None


def availability() -> dict[str, Any]:
    """What is provisioned, checked rather than assumed."""
    checks = {
        "gocognit": GOCOGNIT,
        "go": GO,
        "pmd": PMD,
        "node": NODE,
        "nodepkgs-g0": NODE_MODULES_G0,
        "pyref": PYREF,
    }
    found = {
        name: {"path": str(path), "present": Path(path).exists()}
        for name, path in checks.items()
    }
    return {
        "reference_root": str(REFERENCE_ROOT),
        "tools": found,
        "all_provisioned": all(item["present"] for item in found.values()),
        "go_toolchain_pins": dict(GO_TOOLCHAIN_PINS),
    }
