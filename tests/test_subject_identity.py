"""Regression cover for `subject_key_of` reading any Mapping, not only `dict`.

The defect: the branch that reads a recorded `subject_key` tested
`isinstance(result, dict)`. `validation.artifact_io` hands out `mappingproxy`,
so a repository read straight from `ImmutableRunView.repositories` missed that
branch, fell through to the attribute lookup, found no `.repository_url`
attribute either, and returned the URL-derived fallback **for an empty URL**.

It never raised. It returned a plausible-looking key, and every subject in a run
returned the SAME one, so:

* a lookup keyed on it matched nothing (an empty result reads as "no rows"), and
* a sort keyed on it did nothing (a stable sort preserves input order).

Both failure modes are silent, which is why these cases assert the *collision*
as well as the value: a test that only checked one subject would still pass
against the bug if that subject happened to be the fallback.

The two paths are covered separately because they broke separately:

* a **1.7+** artifact records `subject_key` and must return it verbatim;
* a **pre-1.7** artifact records none, and must return the same canonical key
  derived from its `repository_url` that a current run would produce — the
  cross-generation join the function exists to guarantee.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterator, Mapping

from modules.subject import legacy_subject_identity, subject_key_of
from validation.artifact_io.reader import open_run

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
HISTORICAL = REPOSITORY_ROOT / "tests" / "fixtures" / "historical"

#: What an empty/absent URL degrades to. Every wrong answer the defect produced
#: was this value, so it is the thing to assert *against*.
EMPTY_URL_FALLBACK = legacy_subject_identity(None).subject_key


class _CustomMapping(Mapping):
    """A Mapping that is not a dict and not a mappingproxy.

    Present so the fix is pinned to the Mapping protocol rather than to the two
    concrete types that happen to reach this function today.
    """

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = dict(data)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


class _AttributeResult:
    """An object carrying identity as attributes rather than as items."""

    def __init__(self, subject_key=None, repository_url=None) -> None:
        if subject_key is not None:
            self.subject_key = subject_key
        if repository_url is not None:
            self.repository_url = repository_url


def _first_run_directory(fixture: str) -> Path:
    manifests = sorted(Path(HISTORICAL / fixture).rglob("run_manifest.json"))
    if not manifests:
        raise AssertionError(f"mandatory historical fixture missing: {fixture}")
    return manifests[0].parent


class RecordedKeyTests(unittest.TestCase):
    """A recorded key is returned verbatim, whatever Mapping carries it."""

    RECORDED = {
        "subject_key": "local:arch-bench:3eb89fd3ee0330f4",
        "repository_url": None,
    }

    def test_a_plain_dict_returns_the_recorded_key(self):
        self.assertEqual(subject_key_of(dict(self.RECORDED)), self.RECORDED["subject_key"])

    def test_a_mappingproxy_returns_the_recorded_key(self):
        # The regression. Before the fix this returned EMPTY_URL_FALLBACK.
        self.assertEqual(
            subject_key_of(MappingProxyType(dict(self.RECORDED))),
            self.RECORDED["subject_key"],
        )

    def test_a_mappingproxy_does_not_degrade_to_the_empty_url_fallback(self):
        self.assertNotEqual(
            subject_key_of(MappingProxyType(dict(self.RECORDED))), EMPTY_URL_FALLBACK
        )

    def test_any_mapping_implementation_returns_the_recorded_key(self):
        self.assertEqual(
            subject_key_of(_CustomMapping(dict(self.RECORDED))),
            self.RECORDED["subject_key"],
        )

    def test_the_dict_copy_workaround_is_now_a_no_op(self):
        # Call sites that already wrote `subject_key_of(dict(row))` were working
        # around this defect. The copies must stay correct, not merely tolerated.
        proxy = MappingProxyType(dict(self.RECORDED))
        self.assertEqual(subject_key_of(proxy), subject_key_of(dict(proxy)))


class LegacyFallbackTests(unittest.TestCase):
    """A pre-1.7 record still derives its key from the repository URL."""

    def test_a_mappingproxy_without_a_recorded_key_uses_its_repository_url(self):
        record = MappingProxyType(
            {"repository_url": "https://github.com/7ep/demo"}
        )
        self.assertEqual(subject_key_of(record), "github.com/7ep/demo")
        self.assertNotEqual(subject_key_of(record), EMPTY_URL_FALLBACK)

    def test_a_mapping_with_neither_key_nor_url_still_degrades_deterministically(self):
        self.assertEqual(subject_key_of(MappingProxyType({})), EMPTY_URL_FALLBACK)

    def test_an_empty_recorded_key_falls_through_to_the_url(self):
        record = MappingProxyType(
            {"subject_key": "", "repository_url": "https://github.com/7ep/demo"}
        )
        self.assertEqual(subject_key_of(record), "github.com/7ep/demo")


class AttributeCarrierTests(unittest.TestCase):
    """The non-Mapping branch is unchanged and still reachable."""

    def test_an_object_with_a_subject_key_attribute_is_read(self):
        self.assertEqual(
            subject_key_of(_AttributeResult(subject_key="local:demo")), "local:demo"
        )

    def test_an_object_without_a_key_falls_back_to_its_url_attribute(self):
        self.assertEqual(
            subject_key_of(_AttributeResult(repository_url="https://github.com/7ep/demo")),
            "github.com/7ep/demo",
        )

    def test_an_object_carrying_neither_degrades_deterministically(self):
        self.assertEqual(subject_key_of(_AttributeResult()), EMPTY_URL_FALLBACK)


class ArtifactReaderSeamTests(unittest.TestCase):
    """The real seam: what `ImmutableRunView.repositories` actually yields.

    These read tracked historical fixtures rather than hand-built records. A
    hand-built mapping proves the branch; only the reader proves that the branch
    is the one the reader's output takes.
    """

    def test_the_reader_yields_a_mapping_that_is_not_a_dict(self):
        view = open_run(_first_run_directory("artifact-1.3.0-run"))
        record = view.repositories[0]
        self.assertIsInstance(record, Mapping)
        self.assertNotIsInstance(record, dict)

    def test_reader_records_do_not_all_collapse_onto_one_key(self):
        # The defect's signature. This fixture holds 12 distinct repositories;
        # before the fix every one of them returned EMPTY_URL_FALLBACK, so a
        # per-subject lookup found nothing and a per-subject sort was inert.
        view = open_run(_first_run_directory("artifact-1.3.0-run"))
        keys = [subject_key_of(record) for record in view.repositories]
        self.assertGreater(len(view.repositories), 1)
        self.assertEqual(len(set(keys)), len(keys))
        self.assertNotIn(EMPTY_URL_FALLBACK, keys)

    def test_a_pre_17_reader_record_joins_on_its_url_derived_key(self):
        view = open_run(_first_run_directory("artifact-1.3.0-run"))
        for record in view.repositories:
            url = record.get("repository_url")
            if not url:
                continue
            with self.subTest(url=url):
                self.assertEqual(
                    subject_key_of(record),
                    legacy_subject_identity(url).subject_key,
                )

    def test_reading_through_the_reader_agrees_with_reading_a_copy(self):
        for fixture in (
            "artifact-1.3.0-run",
            "artifact-1.4.0-run",
            "artifact-1.5.0-known-exceptions",
        ):
            view = open_run(_first_run_directory(fixture))
            for record in view.repositories:
                with self.subTest(fixture=fixture, url=record.get("repository_url")):
                    self.assertEqual(subject_key_of(record), subject_key_of(dict(record)))


if __name__ == "__main__":
    unittest.main()
