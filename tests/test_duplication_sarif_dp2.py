"""ArchLens 4.0 Duplication SARIF and text integration, DP2.

A clone group covers several places. DP1 gave the finding one of them as its
primary location and put the rest in evidence; DP2 projects the rest as SARIF
`relatedLocations` and shows them in the default text output.

**SARIF stays a projection.** It does not evaluate duplication, does not
recompute a group, and does not resolve identity. It reads exactly one
generically-named evidence key -- `related_locations` -- which the EVALUATOR
wrote, and it knows nothing about clone groups: `SarifKnowsNothingAboutDuplication`
exists to keep it that way, because a family-specific branch in the projector
would be a second place that understands duplication.

The anti-fabrication guard is the point of the validator extension. A second
locations array is a second place to invent, so every emitted related location
must trace back to the finding's own evidence, be portable, be ordered, and not
repeat the primary.
"""

from __future__ import annotations

import ast
import copy
import json
import sys
import unittest
from pathlib import Path

from modules.policy import check as check_module
from modules.policy import sarif as sarif_module

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_duplication_policy_core_dp1 import (  # noqa: E402
    SUBJECT,
    clean_document,
    document,
    evaluate,
    failed_structural_document,
    lexical_only_document,
    only,
    policy,
)
from test_hotspot_policy_core_h1 import (  # noqa: E402
    hotspot_document,
    hotspot_row,
)
from test_hotspot_policy_core_h1 import evaluate as hotspot_evaluate  # noqa: E402
from test_hotspot_policy_core_h1 import policy as hotspot_policy  # noqa: E402

LEXICAL_METRIC = "duplication_group.lexical_occurrence_count"
STRUCTURAL_METRIC = "duplication_group.structural_occurrence_count"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def group_result(metric: str = LEXICAL_METRIC, threshold: int = 1, **kwargs):
    """One check result carrying exactly one duplication group finding."""
    payload = kwargs.pop("payload", None) or document()
    return evaluate(policy(metric, threshold=threshold, **kwargs), payload)


def projected(result) -> dict:
    return sarif_module.project_sarif(result)


def sole_result(sarif: dict) -> dict:
    results = sarif["runs"][0]["results"]
    assert len(results) == 1, f"expected one SARIF result, got {len(results)}"
    return results[0]


def uris(locations) -> list[str]:
    return [
        item["physicalLocation"]["artifactLocation"]["uri"]
        for item in locations or []
    ]


def coordinates(locations) -> list[tuple]:
    out = []
    for item in locations or []:
        physical = item["physicalLocation"]
        region = physical.get("region") or {}
        out.append((
            physical["artifactLocation"]["uri"],
            region.get("startLine"),
            region.get("endLine"),
        ))
    return out


# --------------------------------------------------------------------------
# The projection
# --------------------------------------------------------------------------

class RelatedLocationTests(unittest.TestCase):
    def test_a_multi_occurrence_group_projects_every_other_place(self):
        payload = document()
        result = group_result(payload=payload)
        sarif = projected(result)
        item = sole_result(sarif)

        group = payload["lexical_groups"][0]
        every = sorted(
            (occurrence["path"], occurrence["start_line"], occurrence["end_line"])
            for occurrence in group["occurrences"]
        )
        primary = coordinates(item["locations"])
        related = coordinates(item["relatedLocations"])

        self.assertEqual(len(primary), 1)
        self.assertEqual(len(related), len(every) - 1)
        # Together they are exactly the group, each place once.
        self.assertEqual(sorted(primary + related), every)
        self.assertEqual(sarif_module.validate_sarif_subset(sarif), [])

    def test_the_primary_location_is_never_repeated(self):
        item = sole_result(projected(group_result()))
        self.assertNotIn(
            coordinates(item["locations"])[0], coordinates(item["relatedLocations"])
        )

    def test_related_locations_are_ordered_by_path_then_lines(self):
        item = sole_result(projected(group_result()))
        found = coordinates(item["relatedLocations"])
        self.assertEqual(found, sorted(found))
        self.assertEqual(uris(item["relatedLocations"]), ["src/b.py", "src/c.py"])

    def test_a_structural_group_projects_the_same_way(self):
        payload = document()
        item = sole_result(projected(
            group_result(STRUCTURAL_METRIC, threshold=1, payload=payload)
        ))
        self.assertEqual(len(item["relatedLocations"]), 2)
        self.assertEqual(
            sarif_module.validate_sarif_subset(projected(
                group_result(STRUCTURAL_METRIC, threshold=1, payload=document())
            )),
            [],
        )

    def test_a_single_place_finding_gains_no_related_locations(self):
        """A hotspot finding covers one file, so the key must not appear."""
        result = hotspot_evaluate(
            hotspot_policy("hotspot_file.churn_commits", threshold=5),
            hotspot_document([hotspot_row("src/a.py", commits=90)]),
        )
        for item in projected(result)["runs"][0]["results"]:
            with self.subTest(rule=item["ruleId"]):
                self.assertNotIn("relatedLocations", item)

    def test_a_repository_scope_finding_gains_no_related_locations(self):
        result = evaluate(
            policy("repository.duplication_lexical_group_count", threshold=0),
            document(),
        )
        item = sole_result(projected(result))
        self.assertNotIn("relatedLocations", item)
        self.assertNotIn("locations", item)


class OccurrenceCapTests(unittest.TestCase):
    def test_related_locations_respect_the_occurrence_cap(self):
        cap = check_module.DUPLICATION_OCCURRENCE_EVIDENCE_CAP
        payload = document()
        group = payload["lexical_groups"][0]
        template = dict(group["occurrences"][0])
        group["occurrences"] = [
            {**template, "path": f"src/gen{index:03d}.py"}
            for index in range(cap + 12)
        ]
        group["occurrence_count"] = cap + 12
        group["file_count"] = cap + 12

        evidence = check_module._duplication_group_evidence(group, "lexical")
        # The cap applies to the occurrence list, and the related list is that
        # list minus the primary.
        self.assertEqual(len(evidence["duplication_occurrences"]), cap)
        self.assertEqual(len(evidence["related_locations"]), cap - 1)
        # The EXACT count survives the cap.
        self.assertEqual(evidence["duplication_occurrence_count"], cap + 12)
        self.assertTrue(evidence["duplication_occurrences_truncated"])

    def test_a_duplicate_coordinate_is_emitted_once(self):
        """Two units at one place is one PLACE; SARIF must not show it twice."""
        payload = document()
        group = payload["lexical_groups"][0]
        first = dict(group["occurrences"][0])
        second = dict(group["occurrences"][1])
        group["occurrences"] = [
            first,
            {**second, "unit_kind": "callable_body"},
            {**second, "unit_kind": "block"},
        ]
        evidence = check_module._duplication_group_evidence(group, "lexical")
        self.assertEqual(len(evidence["related_locations"]), 1)


class NonPortableOccurrenceTests(unittest.TestCase):
    """A refused location loses that URI, never its neighbours, never the finding.

    **A valid Duplication document cannot contain such a path.**
    `validate_duplication_document` runs `validate_relative_path` over every
    occurrence, so an absolute or escaping path is refused by the analysis
    contract long before Policy sees it -- verified by
    `test_the_analysis_contract_already_refuses_these_paths` below.

    The projector re-checks anyway, and that is the point: `normalize_repository_path`
    is the no-leak boundary of the SARIF projection and may not assume its input
    was already clean. These tests therefore inject at the boundary the projector
    actually defends -- the finding record it is handed -- rather than through a
    document its own producer would reject for an unrelated reason.
    """

    def _with_bad_related(self, path: str):
        """One finding whose SECOND place is not repository relative."""
        result = evaluate(policy(LEXICAL_METRIC, threshold=1), document())
        finding = only(result)
        related = finding["evidence"]["related_locations"]
        self.assertEqual(len(related), 2)
        related[0]["path"] = path
        return sole_result(projected(result))

    def test_the_analysis_contract_already_refuses_these_paths(self):
        from modules.duplication.output import (
            DuplicationOutputError,
            validate_duplication_document,
        )

        for path in (
            "/home/someone/checkout/src/b.py", "C:/checkout/src/b.py",
            "../outside/src/b.py",
        ):
            with self.subTest(path=path):
                payload = document()
                payload["lexical_groups"][0]["occurrences"][1]["path"] = path
                with self.assertRaises(DuplicationOutputError):
                    validate_duplication_document(payload)

    def test_an_absolute_occurrence_is_omitted_and_counted(self):
        item = self._with_bad_related("/home/someone/checkout/src/b.py")
        self.assertEqual(uris(item["relatedLocations"]), ["src/c.py"])
        self.assertEqual(
            item["properties"]["archlens"]["relatedLocationsOmitted"], 1
        )

    def test_a_windows_occurrence_is_omitted_and_counted(self):
        item = self._with_bad_related("C:/checkout/src/b.py")
        self.assertEqual(uris(item["relatedLocations"]), ["src/c.py"])
        self.assertEqual(
            item["properties"]["archlens"]["relatedLocationsOmitted"], 1
        )

    def test_an_escaping_occurrence_is_omitted_and_counted(self):
        item = self._with_bad_related("../outside/src/b.py")
        self.assertEqual(uris(item["relatedLocations"]), ["src/c.py"])
        self.assertEqual(
            item["properties"]["archlens"]["relatedLocationsOmitted"], 1
        )

    def test_the_finding_itself_is_never_dropped(self):
        item = self._with_bad_related("/home/someone/src/b.py")
        self.assertTrue(item["ruleId"])
        self.assertTrue(item["message"]["text"])
        self.assertIn("archlensFindingId/v1", item["partialFingerprints"])
        self.assertEqual(item["properties"]["archlens"]["observedValue"], 3)

    def test_no_omission_property_when_nothing_was_omitted(self):
        item = sole_result(projected(group_result()))
        self.assertNotIn("relatedLocationsOmitted", item["properties"]["archlens"])

    def test_a_non_portable_primary_does_not_suppress_the_rest(self):
        """One bad URI must not blind a reader to the places that are fine."""
        result = evaluate(policy(LEXICAL_METRIC, threshold=1), document())
        finding = only(result)
        finding["path"] = "/home/someone/src/a.py"
        item = sole_result(projected(result))
        self.assertNotIn("locations", item)
        self.assertEqual(
            item["properties"]["archlens"]["locationOmitted"],
            "path_is_not_repository_relative",
        )
        self.assertEqual(uris(item["relatedLocations"]), ["src/b.py", "src/c.py"])

    def test_a_refused_location_never_leaks_the_machine_path(self):
        result = evaluate(policy(LEXICAL_METRIC, threshold=1), document())
        only(result)["evidence"]["related_locations"][0]["path"] = (
            "/home/someone/src/b.py"
        )
        sarif = projected(result)
        serialized = json.dumps(sarif, ensure_ascii=False)
        # The path is gone from the LOCATION and from the evidence copy alike:
        # `_portable_json` replaces it inside `properties.archlens.evidence`,
        # so refusing the location cannot be undone by reading the evidence.
        self.assertNotIn("/home/someone", serialized)
        self.assertEqual(sarif_module.validate_sarif_subset(sarif), [])


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

class ValidatorTests(unittest.TestCase):
    def _sarif(self) -> dict:
        return projected(group_result())

    def test_a_valid_projection_is_accepted(self):
        self.assertEqual(sarif_module.validate_sarif_subset(self._sarif()), [])

    def test_a_fabricated_related_location_is_rejected(self):
        sarif = self._sarif()
        sole_result(sarif)["relatedLocations"].append({
            "physicalLocation": {
                "artifactLocation": {"uri": "src/never_analyzed.py"},
                "region": {"startLine": 1, "endLine": 9},
            }
        })
        errors = sarif_module.validate_sarif_subset(sarif)
        self.assertTrue(any("not present in the finding" in item for item in errors))

    def test_a_related_location_absent_from_evidence_is_rejected(self):
        """Editing the evidence away is caught too, not only adding a location."""
        sarif = self._sarif()
        sole_result(sarif)["properties"]["archlens"]["evidence"][
            "related_locations"
        ] = []
        errors = sarif_module.validate_sarif_subset(sarif)
        self.assertEqual(
            sum("not present in the finding" in item for item in errors), 2
        )

    def test_an_out_of_order_related_location_is_rejected(self):
        sarif = self._sarif()
        item = sole_result(sarif)
        item["relatedLocations"] = list(reversed(item["relatedLocations"]))
        errors = sarif_module.validate_sarif_subset(sarif)
        self.assertTrue(any("not deterministic" in error for error in errors))

    def test_a_related_location_repeating_the_primary_is_rejected(self):
        sarif = self._sarif()
        item = sole_result(sarif)
        item["relatedLocations"].insert(0, copy.deepcopy(item["locations"][0]))
        errors = sarif_module.validate_sarif_subset(sarif)
        self.assertTrue(
            any("repeats the primary location" in error for error in errors)
        )

    def test_a_duplicated_related_location_is_rejected(self):
        sarif = self._sarif()
        item = sole_result(sarif)
        item["relatedLocations"].append(copy.deepcopy(item["relatedLocations"][-1]))
        errors = sarif_module.validate_sarif_subset(sarif)
        self.assertTrue(
            any("repeats a related location" in error for error in errors)
        )

    def test_a_non_portable_related_location_is_rejected(self):
        sarif = self._sarif()
        item = sole_result(sarif)
        item["relatedLocations"][0]["physicalLocation"]["artifactLocation"][
            "uri"
        ] = "../outside/src/b.py"
        errors = sarif_module.validate_sarif_subset(sarif)
        self.assertTrue(any("not portable" in error for error in errors))

    def test_a_malformed_related_location_is_rejected(self):
        sarif = self._sarif()
        sole_result(sarif)["relatedLocations"].append({"nonsense": True})
        errors = sarif_module.validate_sarif_subset(sarif)
        self.assertTrue(
            any("not a physical location" in error for error in errors)
        )

    def test_a_portable_but_unnormalized_evidence_path_still_matches(self):
        """The emitted URI is normalized; the evidence keeps what was recorded.

        Comparing the two raw made the projector refuse its OWN honest output
        the moment a producer wrote `src/./b.py` -- portable, but not in normal
        form. `project_sarif` self-validates, so the failure was a raised
        `SarifProjectionError` rather than a bad document: the projection of a
        perfectly good finding died on a spelling. Both sides are normalized now,
        so the check compares places rather than spellings.
        """
        result = evaluate(policy(LEXICAL_METRIC, threshold=1), document())
        only(result)["evidence"]["related_locations"][0]["path"] = "src/./b.py"
        sarif = projected(result)
        item = sole_result(sarif)
        self.assertEqual(uris(item["relatedLocations"]), ["src/b.py", "src/c.py"])
        self.assertEqual(sarif_module.validate_sarif_subset(sarif), [])
        self.assertNotIn(
            "relatedLocationsOmitted", item["properties"]["archlens"]
        )

    def test_an_unnormalizable_evidence_path_backs_no_location(self):
        """The other direction: it may not licence a location either."""
        result = evaluate(policy(LEXICAL_METRIC, threshold=1), document())
        only(result)["evidence"]["related_locations"][0]["path"] = "/etc/passwd"
        sarif = projected(result)
        item = sole_result(sarif)
        self.assertEqual(uris(item["relatedLocations"]), ["src/c.py"])
        self.assertEqual(
            item["properties"]["archlens"]["relatedLocationsOmitted"], 1
        )
        self.assertEqual(sarif_module.validate_sarif_subset(sarif), [])

    def test_a_related_locations_object_is_rejected(self):
        sarif = self._sarif()
        sole_result(sarif)["relatedLocations"] = {"not": "an array"}
        errors = sarif_module.validate_sarif_subset(sarif)
        self.assertTrue(any("not an array" in error for error in errors))


# --------------------------------------------------------------------------
# Determinism and preservation
# --------------------------------------------------------------------------

class DeterminismTests(unittest.TestCase):
    def test_two_projections_are_byte_identical(self):
        first = sarif_module.serialize_sarif(projected(group_result()))
        second = sarif_module.serialize_sarif(projected(group_result()))
        self.assertEqual(first, second)

    def test_the_order_comes_from_the_sort_not_from_the_input(self):
        """A valid document is already canonically ordered, so this is tested
        on the builder directly: the Duplication contract refuses an
        out-of-order occurrence array, and a test that fed one through
        `evaluate` would be rejected at admission for an unrelated reason.

        The sort exists so the projection stays deterministic even if a future
        producer orders its occurrences differently.
        """
        payload = document()
        occurrences = payload["lexical_groups"][0]["occurrences"]
        forward = check_module._related_location_list(occurrences)
        # Same primary, remaining places supplied in the opposite order.
        backward = check_module._related_location_list(
            [occurrences[0]] + list(reversed(occurrences[1:]))
        )
        self.assertEqual(forward, backward)
        self.assertEqual(
            [item["path"] for item in forward], ["src/b.py", "src/c.py"]
        )

    def test_no_timestamp_reaches_the_projection(self):
        sarif = projected(group_result())
        serialized = json.dumps(sarif, ensure_ascii=False)
        for word in ("startTimeUtc", "endTimeUtc", "timestamp"):
            self.assertNotIn(word, serialized)


class PreservationTests(unittest.TestCase):
    """Everything H2-A pinned still holds."""

    def test_fingerprints_severity_and_baseline_are_unchanged(self):
        item = sole_result(projected(group_result()))
        self.assertIn("archlensFindingId/v1", item["partialFingerprints"])
        self.assertEqual(
            item["partialFingerprints"]["archlensFindingId/v1"],
            item["properties"]["archlens"]["findingId"],
        )
        self.assertEqual(item["level"], "error")
        self.assertNotIn("baselineState", item)

    def test_the_finding_evidence_survives_intact(self):
        payload = document()
        finding = only(evaluate(policy(LEXICAL_METRIC, threshold=1), payload))
        projected_evidence = sole_result(
            projected(evaluate(policy(LEXICAL_METRIC, threshold=1), payload))
        )["properties"]["archlens"]["evidence"]
        for key in (
            "duplication_kind", "duplication_group_id", "duplication_fingerprint",
            "duplication_fingerprint_version", "duplication_distribution",
            "duplication_occurrence_count", "duplication_file_count",
            "duplication_note",
        ):
            with self.subTest(key=key):
                self.assertEqual(
                    projected_evidence[key], finding["evidence"][key]
                )
        self.assertEqual(
            len(projected_evidence["duplication_occurrences"]),
            len(finding["evidence"]["duplication_occurrences"]),
        )

    def test_the_hotspot_projection_is_unchanged(self):
        result = hotspot_evaluate(
            hotspot_policy("hotspot_file.churn_commits", threshold=5),
            hotspot_document([
                hotspot_row("src/a.py", commits=90),
                hotspot_row("src/b.py", commits=80),
            ]),
        )
        sarif = projected(result)
        self.assertEqual(sarif_module.validate_sarif_subset(sarif), [])
        for item in sarif["runs"][0]["results"]:
            with self.subTest(rule=item["ruleId"]):
                self.assertNotIn("relatedLocations", item)
                self.assertNotIn(
                    "relatedLocationsOmitted", item["properties"]["archlens"]
                )
                self.assertIn("hotspot_classification", (
                    item["properties"]["archlens"].get("evidence") or {}
                ))

    def test_a_waived_group_still_projects_every_place(self):
        """A waiver suppresses the RESULT, not the evidence behind it.

        A reader who lifts the waiver must be able to see what was waived, so a
        suppressed finding keeps its locations, its related locations and its
        group evidence.
        """
        from modules.policy.document_v2 import load_any_policy

        waived = load_any_policy({
            "policy_document_format_version": "2.2.0",
            "name": "duplication policy",
            "metric_rules": [{
                "id": "dup.rule", "metric": LEXICAL_METRIC,
                "operator": "gt", "threshold": 1,
            }],
            "waivers": [{
                "rule_id": "dup.rule", "subject_key": SUBJECT,
                "reason": "accepted vendored clone", "expires_on": "2099-01-01",
            }],
        })
        result = evaluate(waived, document())
        self.assertEqual(result["findings"], [])
        self.assertEqual(len(result["waived_findings"]), 1)

        sarif = projected(result)
        item = sole_result(sarif)
        self.assertTrue(item["suppressions"])
        self.assertEqual(uris(item["relatedLocations"]), ["src/b.py", "src/c.py"])
        self.assertEqual(sarif_module.validate_sarif_subset(sarif), [])

    def test_a_collapsed_duplication_finding_has_no_related_locations(self):
        """No group, no places: `not_evaluable` is not located anywhere."""
        for label, payload in (
            ("kind not requested", lexical_only_document()),
            ("kind failed", failed_structural_document()),
            ("no clones", clean_document()),
        ):
            with self.subTest(case=label):
                result = evaluate(
                    policy(STRUCTURAL_METRIC, threshold=0), payload
                )
                sarif = projected(result)
                self.assertEqual(sarif_module.validate_sarif_subset(sarif), [])
                self.assertEqual(sarif["runs"][0]["results"], [])
                # `not_evaluable` becomes a notification; `not_applicable`
                # becomes neither, which is why the list is only asserted to be
                # free of locations rather than asserted to be non-empty.
                for item in sarif["runs"][0]["invocations"][0].get(
                    "toolExecutionNotifications"
                ) or []:
                    self.assertNotIn("relatedLocations", item)
                    self.assertNotIn(
                        "relatedLocationsOmitted",
                        item["properties"]["archlens"],
                    )


class SarifKnowsNothingAboutDuplication(unittest.TestCase):
    """The projector must stay family-agnostic. Asserted, not assumed."""

    SOURCE = Path(sarif_module.__file__).read_text(encoding="utf-8")

    #: Words that would mean the projector understands a specific analysis.
    FAMILY_WORDS = (
        "duplication", "clone", "lexical", "structural", "occurrence",
        "hotspot", "churn", "fingerprintversion",
    )

    @classmethod
    def _executable_strings(cls) -> list[str]:
        """Every identifier and non-docstring literal in the module.

        Comments and docstrings are excluded DELIBERATELY. `sarif.py` explains
        at length which family-specific key it refuses to read, and naming the
        thing you are refusing to do is exactly what that comment is for -- the
        pre-existing hotspot comment from H2-A is there for the same reason.
        What must not exist is a duplication concept in the CODE: an attribute,
        an identifier, or a string literal the projector actually branches on.

        Docstrings are found positionally (first statement of a module, class,
        or function body) rather than by content, so a real string literal that
        happens to look like prose is still checked.
        """
        tree = ast.parse(cls.SOURCE)
        docstrings: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(
                node,
                (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                body = getattr(node, "body", None) or []
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    docstrings.add(id(body[0].value))

        found: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                found.append(node.id)
            elif isinstance(node, ast.Attribute):
                found.append(node.attr)
            elif isinstance(node, ast.keyword) and node.arg:
                found.append(node.arg)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                found.append(node.name)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) not in docstrings:
                    found.append(node.value)
        return [item.lower() for item in found]

    def test_the_projector_names_no_family_concept_in_code(self):
        executable = self._executable_strings()
        for word in self.FAMILY_WORDS:
            with self.subTest(word=word):
                offenders = [item for item in executable if word in item]
                self.assertEqual(offenders, [], f"{word!r} reached the code")

    def test_the_family_words_only_ever_appear_in_prose(self):
        """The counterpart: they DO appear, and only in comments and docstrings.

        Without this, the test above would keep passing if someone deleted the
        comment that explains the boundary, and the boundary would quietly stop
        being documented.
        """
        lowered = self.SOURCE.lower()
        self.assertIn("duplication", lowered)
        self.assertIn("has no per-family branch", lowered)

    def test_the_projector_imports_nothing_from_an_analysis_module(self):
        tree = ast.parse(self.SOURCE)
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom):
                names.append(node.module or "")
            elif isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)
            for name in names:
                self.assertFalse(name.startswith("modules.duplication"), name)
                self.assertFalse(name.startswith("modules.hotspots"), name)

    def test_the_related_location_key_is_the_only_contract(self):
        self.assertEqual(sarif_module.RELATED_LOCATIONS_KEY, "related_locations")


# --------------------------------------------------------------------------
# Text output
# --------------------------------------------------------------------------

def render(result) -> str:
    from modules.cli.check_command import render_text

    return render_text(result)


class TextOutputTests(unittest.TestCase):
    def test_a_violated_group_shows_its_published_figures(self):
        payload = document()
        rendered = render(
            evaluate(policy(STRUCTURAL_METRIC, threshold=1), payload)
        )
        group = payload["structural_groups"][0]
        self.assertIn("structural clone group", rendered)
        self.assertIn(f"{group['occurrence_count']} occurrence(s)", rendered)
        self.assertIn(f"{group['file_count']} file(s)", rendered)
        self.assertIn(
            f"{group['source_span_line_count']} merged span line(s)", rendered
        )
        self.assertIn(group["distribution"], rendered)

    def test_every_occurrence_is_listed(self):
        payload = document()
        rendered = render(evaluate(policy(LEXICAL_METRIC, threshold=1), payload))
        for occurrence in payload["lexical_groups"][0]["occurrences"]:
            with self.subTest(path=occurrence["path"]):
                self.assertIn(
                    f"{occurrence['path']}:{occurrence['start_line']}"
                    f"-{occurrence['end_line']}",
                    rendered,
                )

    def test_a_lexical_group_shows_no_span_line_count(self):
        """The Duplication contract publishes one only for structural groups."""
        rendered = render(
            evaluate(policy(LEXICAL_METRIC, threshold=1), document())
        )
        self.assertIn("lexical clone group", rendered)
        self.assertNotIn("merged span line(s)", rendered)

    def test_the_kind_and_its_status_are_shown_when_unavailable(self):
        for label, payload, status in (
            ("not requested", lexical_only_document(), "not_requested"),
            ("failed", failed_structural_document(), "failed"),
        ):
            with self.subTest(case=label):
                rendered = render(
                    evaluate(policy(STRUCTURAL_METRIC, threshold=0), payload)
                )
                self.assertIn("duplication kind: structural", rendered)
                self.assertIn(f"counts.structural.status = {status}", rendered)

    def test_a_measured_empty_population_shows_its_kind(self):
        rendered = render(
            evaluate(policy(STRUCTURAL_METRIC, threshold=0), clean_document())
        )
        self.assertIn("Not applicable", rendered)
        self.assertIn("duplication kind: structural", rendered)

    def test_the_evidence_admission_state_is_shown(self):
        rendered = render(evaluate(policy(LEXICAL_METRIC, threshold=1), document()))
        self.assertIn("evidence:", rendered)
        self.assertIn("duplication", rendered)
        self.assertIn("admitted", rendered)
        self.assertIn(SUBJECT, rendered)

    def test_a_missing_document_is_shown_as_such(self):
        rendered = render(evaluate(policy(LEXICAL_METRIC, threshold=1), None))
        self.assertIn("not_supplied", rendered)
        self.assertIn("NOT evaluable", rendered)


class TextAndJsonAgreeTests(unittest.TestCase):
    """Text renders the result document. It may not disagree with it."""

    def test_the_counts_in_text_are_the_counts_in_json(self):
        payload = document()
        result = evaluate(policy(STRUCTURAL_METRIC, threshold=1), payload)
        rendered = render(result)
        evidence = only(result)["evidence"]
        self.assertIn(
            f"{evidence['duplication_occurrence_count']} occurrence(s)", rendered
        )
        self.assertIn(f"{evidence['duplication_file_count']} file(s)", rendered)
        self.assertIn(str(evidence["duplication_distribution"]), rendered)

    def test_the_exact_count_is_shown_even_when_the_list_is_capped(self):
        """A capped list must never be reported as the count."""
        cap = check_module.DUPLICATION_OCCURRENCE_EVIDENCE_CAP
        result = evaluate(policy(LEXICAL_METRIC, threshold=1), document())
        finding = only(result)
        finding["evidence"]["duplication_occurrence_count"] = cap + 12
        finding["evidence"]["duplication_occurrences_truncated"] = True
        rendered = render(result)
        self.assertIn(f"{cap + 12} occurrence(s)", rendered)
        self.assertIn("in total", rendered)
        self.assertIn("3 listed", rendered)

    def test_the_admission_state_agrees_for_every_case(self):
        cases = (
            ("admitted", document(), None),
            ("not supplied", None, None),
            ("kind not requested", lexical_only_document(), None),
        )
        for label, payload, _ in cases:
            with self.subTest(case=label):
                result = evaluate(policy(LEXICAL_METRIC, threshold=1), payload)
                rendered = render(result)
                self.assertIn(
                    result["evidence"]["duplication"]["admission"], rendered
                )

    def test_text_invents_no_number_the_result_does_not_carry(self):
        """Every integer in the group detail line comes from the evidence."""
        import re

        payload = document()
        result = evaluate(policy(STRUCTURAL_METRIC, threshold=1), payload)
        evidence = only(result)["evidence"]
        detail = [
            line for line in render(result).splitlines()
            if "clone group:" in line
        ]
        self.assertEqual(len(detail), 1)
        allowed = {
            evidence["duplication_occurrence_count"],
            evidence["duplication_file_count"],
            evidence["duplication_source_span_line_count"],
        }
        for number in re.findall(r"\d+", detail[0]):
            with self.subTest(number=number):
                self.assertIn(int(number), allowed)


if __name__ == "__main__":
    unittest.main()
