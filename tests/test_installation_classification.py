"""Focused provenance regressions; installed-wheel/editable lanes supplement these."""
import base64
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from modules import preflight
from modules.vocabularies import CapabilityState, InstallationKind


class Distribution:
    version = "4.0.0"

    def __init__(self, root, *, direct=None, egg=False, foreign=False, corrupt=False):
        self.root = root
        self.direct = direct
        names = (["metrolith.egg-info/PKG-INFO"] if egg else
                 ["metrolith-4.0.0.dist-info/METADATA", "metrolith-4.0.0.dist-info/RECORD"])
        self.files = [metadata.PackagePath(name) for name in names]
        if not egg and direct is None:
            for name in ("pipeline.py", "modules/config.py", "modules/preflight.py"):
                p = metadata.PackagePath(name)
                actual = Path(preflight.__file__).resolve().parent.parent / name
                value = base64.urlsafe_b64encode(hashlib.sha256(actual.read_bytes()).digest()).decode().rstrip("=")
                p.hash = metadata.FileHash("sha256=" + ("bad" if corrupt else value))
                self.files.append(p)
        self.foreign = foreign

    def locate_file(self, entry):
        if not str(entry) or self.direct is not None or self.foreign:
            return self.root / entry
        return Path(preflight.__file__).resolve().parent.parent / entry

    def read_text(self, name):
        return json.dumps(self.direct) if self.direct is not None else None


@pytest.mark.parametrize("kind", ["egg", "foreign", "corrupt", "empty"])
def test_stale_foreign_or_unbound_metadata_is_not_verified(tmp_path, kind):
    dist = Distribution(tmp_path, egg=kind == "egg", foreign=kind == "foreign", corrupt=kind == "corrupt")
    if kind == "empty":
        dist.files = dist.files[:2]
    with patch.object(metadata, "distribution", return_value=dist):
        check = preflight._installation_check()
    assert check.state is CapabilityState.NOT_EVALUATED
    assert not check.blocking


def test_recorded_loaded_modules_are_recognized(tmp_path):
    with patch.object(metadata, "distribution", return_value=Distribution(tmp_path)):
        kind, _ = preflight._distribution_evidence()
    assert kind is InstallationKind.INSTALLED_DISTRIBUTION


@pytest.mark.parametrize("matches", [True, False])
def test_editable_requires_matching_source_provenance(tmp_path, matches):
    root = Path(preflight.__file__).resolve().parent.parent if matches else tmp_path / "foreign"
    dist = Distribution(tmp_path, direct={"url": root.as_uri(), "dir_info": {"editable": True}})
    with patch.object(metadata, "distribution", return_value=dist):
        kind, _ = preflight._distribution_evidence()
    assert kind is (InstallationKind.EDITABLE_INSTALL if matches else InstallationKind.INDETERMINATE)


def test_malformed_editable_provenance_is_not_verified(tmp_path):
    dist = Distribution(tmp_path, direct=["not an object"])
    with patch.object(metadata, "distribution", return_value=dist):
        assert preflight._installation_check().state is CapabilityState.NOT_EVALUATED
